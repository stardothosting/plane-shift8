"""Main orchestration — pull from Linear, push into Plane's Django models.

Designed to be called from a Django management command where the ORM is
already initialised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from . import entity_mapper as mapper
from .checkpoint import ImportCheckpointStore
from .linear_client import LinearClient

logger = logging.getLogger(__name__)

TShirtEstimateScale = {
    1: "XS",
    2: "S",
    3: "M",
    4: "L",
    5: "XL",
}


def resolve_linear_estimate_scale(
    values: set[float],
    estimate_scale: Literal["auto", "points", "tshirt"],
) -> tuple[str, str, list[tuple[int, str]], dict[float, int]]:
    """Return the Plane estimate definition for the given Linear values.

    Returns: (estimate_name, estimate_type, estimate_points, linear_value_to_key)
    where estimate_points is a list of (key, display_value).
    """
    normalized_values = sorted(values)
    use_tshirt = estimate_scale == "tshirt" or (
        estimate_scale == "auto" and set(normalized_values).issubset({1, 2, 3, 4, 5})
    )

    if use_tshirt:
        point_values = [(index, TShirtEstimateScale[index]) for index in sorted(TShirtEstimateScale)]
        value_map = {float(index): index for index in TShirtEstimateScale}
        return "T-Shirt Sizes", "categories", point_values, value_map

    point_values = [(idx + 1, str(val)) for idx, val in enumerate(normalized_values)]
    linear_value_map = {float(val): idx + 1 for idx, val in enumerate(normalized_values)}
    return "Linear Estimates", "points", point_values, linear_value_map


@dataclass
class ImportStats:
    """Simple counters for the import run."""

    teams: int = 0
    states: int = 0
    labels: int = 0
    users_mapped: int = 0
    issues: int = 0
    comments: int = 0
    attachments: int = 0
    relations: int = 0
    estimates: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Teams/Projects : {self.teams}",
            f"States         : {self.states}",
            f"Labels         : {self.labels}",
            f"Users mapped   : {self.users_mapped}",
            f"Issues         : {self.issues}",
            f"Comments       : {self.comments}",
            f"Attachments    : {self.attachments}",
            f"Relations      : {self.relations}",
            f"Estimates      : {self.estimates}",
            f"Skipped (dupes): {self.skipped}",
            f"Errors         : {len(self.errors)}",
        ]
        if self.errors:
            lines.append("---")
            for err in self.errors[:20]:
                lines.append(f"  • {err}")
            if len(self.errors) > 20:
                lines.append(f"  … and {len(self.errors) - 20} more")
        return "\n".join(lines)


class LinearImporter:
    """Orchestrates a full Linear → Plane import.

    Parameters
    ----------
    client:
        An authenticated :class:`LinearClient`.
    workspace_id:
        UUID of the target Plane workspace.
    owner_id:
        UUID of the Plane user who owns the import (used for ``created_by``).
    team_ids:
        Optional whitelist of Linear team IDs to import.  All teams imported
        when ``None``.
    dry_run:
        When ``True``, fetch from Linear but skip all database writes.
    """

    def __init__(
        self,
        client: LinearClient,
        workspace_id: Any,
        owner_id: Any,
        *,
        team_ids: list[str] | None = None,
        team_keys: list[str] | None = None,
        dry_run: bool = False,
        checkpoint_store: ImportCheckpointStore | None = None,
        progress_callback: Any | None = None,
        since: datetime | None = None,
        resume_completed: bool = True,
        authoritative_sync: bool = False,
        mirror_mode: bool = False,
        estimate_scale: Literal["auto", "points", "tshirt"] = "auto",
    ):
        self.client = client
        self.workspace_id = workspace_id
        self.owner_id = owner_id
        self.team_ids = team_ids
        self.team_keys = [key.upper() for key in (team_keys or [])] or None
        self.dry_run = dry_run
        self.checkpoint_store = checkpoint_store
        self.progress_callback = progress_callback
        self.since = since
        self.resume_completed = resume_completed
        self.authoritative_sync = authoritative_sync
        self.mirror_mode = mirror_mode
        self.estimate_scale = estimate_scale
        self.stats = ImportStats()

        # Lookup maps: Linear ID → Plane PK
        self._state_map: dict[str, Any] = {}
        self._label_map: dict[str, Any] = {}
        self._user_map: dict[str, Any] = {}
        self._issue_map: dict[str, Any] = {}  # linear issue id → plane issue pk

    def _progress(self, message: str) -> None:
        if self.progress_callback is None:
            logger.info(message)
            return
        self.progress_callback(message)

    @staticmethod
    def _deleted_count(delete_result: Any) -> int:
        if isinstance(delete_result, tuple):
            return int(delete_result[0])
        return int(delete_result or 0)

    def _canonical_relation_tuple(
        self,
        relation: dict[str, Any],
        *,
        current_linear_issue_id: str,
    ) -> tuple[Any, Any, str] | None:
        rel_type = (relation.get("type") or "").lower()
        related_issue = relation.get("relatedIssue") or {}
        related_linear_id = related_issue.get("id")
        if not related_linear_id:
            return None

        current_pk = self._issue_map.get(current_linear_issue_id)
        related_pk = self._issue_map.get(related_linear_id)
        if not current_pk or not related_pk:
            return None

        if rel_type == "blocks":
            return related_pk, current_pk, "blocked_by"
        if rel_type == "blocked_by":
            return current_pk, related_pk, "blocked_by"
        if rel_type == "duplicate":
            if str(current_pk) <= str(related_pk):
                return current_pk, related_pk, "duplicate"
            return related_pk, current_pk, "duplicate"
        if rel_type == "related":
            if str(current_pk) <= str(related_pk):
                return current_pk, related_pk, "relates_to"
            return related_pk, current_pk, "relates_to"

        return None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> ImportStats:
        """Execute the full import and return statistics."""
        logger.info("Starting Linear import (dry_run=%s)", self.dry_run)
        self._progress("Starting Linear import")

        self._progress("Importing users")
        self._import_users()
        self._progress(f"Imported users: {self.stats.users_mapped} mapped")

        self._progress("Importing labels")
        linear_label_ids = self._import_labels()
        self._progress(f"Imported labels: {self.stats.labels}")
        if self.mirror_mode and not self.dry_run:
            self._prune_missing_labels(linear_label_ids)

        self._progress("Fetching teams")
        teams = self.client.fetch_teams()
        if self.team_ids:
            teams = [t for t in teams if t["id"] in self.team_ids]
        if self.team_keys:
            teams = [t for t in teams if (t.get("key") or "").upper() in self.team_keys]

        self._progress(f"Found {len(teams)} team(s) to process")

        for index, team in enumerate(teams, start=1):
            if (
                self.resume_completed
                and self.checkpoint_store
                and self.checkpoint_store.is_team_done(team["id"])
            ):
                self._progress(
                    f"Skipping completed team {index}/{len(teams)}: {team.get('name')}"
                )
                self.stats.skipped += 1
                continue
            self._progress(
                f"Team {index}/{len(teams)}: {team.get('name')} ({team.get('key')})"
            )
            self._import_team(team)
            if self.checkpoint_store and not self.dry_run:
                self.checkpoint_store.mark_team_done(team["id"])
            self._progress(f"Finished team {index}/{len(teams)}: {team.get('name')}")

        if self.mirror_mode and not self.dry_run and not self.team_ids:
            self._prune_missing_projects({team["id"] for team in teams})

        logger.info("Import finished.\n%s", self.stats.summary())
        self._progress("Import finished")
        return self.stats

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def _import_users(self) -> None:
        """Build a mapping of Linear user emails → Plane user PKs."""
        from plane.db.models import User  # noqa: delayed import

        linear_users = self.client.fetch_users()
        for lu in linear_users:
            email = (lu.get("email") or "").lower().strip()
            if not email:
                continue
            try:
                plane_user = User.objects.get(email=email)
                self._user_map[lu["id"]] = plane_user.pk
                self.stats.users_mapped += 1
                logger.debug("Mapped user %s → %s", email, plane_user.pk)
            except User.DoesNotExist:
                logger.info(
                    "Linear user %s (%s) has no Plane account — skipping",
                    lu.get("name"),
                    email,
                )

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _import_labels(self) -> set[str]:
        """Import workspace-level labels.

        Linear has per-team labels with duplicate names (e.g. "Bug" in every
        team).  Plane enforces unique label names at the workspace level, so
        we deduplicate: first try matching by ``external_id`` (re-run safe),
        then fall back to matching by name.
        """
        from django.db import IntegrityError
        from plane.db.models import Label  # noqa: delayed import

        linear_labels = self.client.fetch_labels()
        linear_label_ids: set[str] = set()
        for ll in linear_labels:
            linear_label_ids.add(ll["id"])
            kwargs = mapper.map_label(ll)
            if self.dry_run:
                self.stats.labels += 1
                continue

            # Already mapped in a prior iteration (duplicate name from another team)?
            if ll["id"] in self._label_map:
                continue

            try:
                label, created = Label.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=None,
                    external_source=mapper.EXTERNAL_SOURCE,
                    external_id=ll["id"],
                    defaults={
                        **kwargs,
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                self._label_map[ll["id"]] = label.pk
                if created:
                    self.stats.labels += 1
                else:
                    self.stats.skipped += 1
            except IntegrityError:
                # A label with this name already exists — reuse it.
                try:
                    label = Label.objects.get(
                        workspace_id=self.workspace_id,
                        project=None,
                        name=kwargs["name"],
                    )
                    self._label_map[ll["id"]] = label.pk
                    self.stats.skipped += 1
                except Label.DoesNotExist:
                    msg = f"Label '{ll.get('name')}': name conflict but lookup failed"
                    logger.error(msg)
                    self.stats.errors.append(msg)
            except Exception as exc:
                msg = f"Label '{ll.get('name')}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        return linear_label_ids

    def _prune_missing_labels(self, linear_label_ids: set[str]) -> None:
        from plane.db.models import Label  # noqa: delayed import

        queryset = Label.objects.filter(
            workspace_id=self.workspace_id,
            project=None,
            external_source=mapper.EXTERNAL_SOURCE,
        )
        if linear_label_ids:
            queryset = queryset.exclude(external_id__in=linear_label_ids)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Pruned {deleted} label record(s) missing from Linear")

    def _prune_missing_projects(self, linear_team_ids: set[str]) -> None:
        from plane.db.models import Project  # noqa: delayed import

        queryset = Project.objects.filter(
            workspace_id=self.workspace_id,
            external_source=mapper.EXTERNAL_SOURCE,
        )
        if linear_team_ids:
            queryset = queryset.exclude(external_id__in=linear_team_ids)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Pruned {deleted} project record(s) missing from Linear")

    # ------------------------------------------------------------------
    # Teams → Projects + States
    # ------------------------------------------------------------------

    def _import_team(self, team: dict[str, Any]) -> None:
        """Import one Linear team as a Plane project with its states and issues."""
        from plane.db.models import Project, ProjectMember, State  # noqa: delayed

        project_kwargs = mapper.map_team_to_project(team)
        project_kwargs["workspace_id"] = self.workspace_id
        project_kwargs["created_by_id"] = self.owner_id
        project_kwargs["updated_by_id"] = self.owner_id

        project = None
        if not self.dry_run:
            try:
                project, created = Project.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    external_source=mapper.EXTERNAL_SOURCE,
                    external_id=team["id"],
                    defaults=project_kwargs,
                )
                if created:
                    self.stats.teams += 1
                    # Add the owner as an admin member of the project
                    ProjectMember.objects.get_or_create(
                        workspace_id=self.workspace_id,
                        project=project,
                        member_id=self.owner_id,
                        defaults={
                            "role": 20,  # Admin
                            "created_by_id": self.owner_id,
                            "updated_by_id": self.owner_id,
                        },
                    )
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"Team/Project '{team.get('name')}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)
                return
        else:
            self.stats.teams += 1

        # States
        team_state_map: dict[str, Any] = {}
        linear_state_ids: set[str] = set()
        for ls in team.get("states", {}).get("nodes", []):
            linear_state_ids.add(ls["id"])
            state_kwargs = mapper.map_state(ls)
            if self.dry_run:
                self.stats.states += 1
                continue
            try:
                state, created = State.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    external_source=mapper.EXTERNAL_SOURCE,
                    external_id=ls["id"],
                    defaults={
                        **state_kwargs,
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                team_state_map[ls["id"]] = state.pk
                self._state_map[ls["id"]] = state.pk
                if created:
                    self.stats.states += 1
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"State '{ls.get('name')}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        if self.mirror_mode and not self.dry_run and project:
            self._prune_missing_states(project, linear_state_ids)

        # Issues (two passes: create, then wire parents)
        since_label = f" since {self.since.isoformat()}" if self.since else ""
        self._progress(f"Fetching issues for {team.get('name')}{since_label}")
        issues = self.client.fetch_issues_for_team(team["id"], since=self.since)
        self._progress(f"Fetched {len(issues)} issue(s) for {team.get('name')}")

        if self.mirror_mode and not self.dry_run and project and self.since is None:
            self._prune_missing_issues(project, {issue["id"] for issue in issues})

        # Build estimate system for this project before importing issues
        estimate_point_map: dict[float, Any] = {}
        if not self.dry_run and project:
            estimate_point_map = self._setup_estimates(issues, project)

        self._import_issues(issues, project, team_state_map, estimate_point_map)

    def _prune_missing_states(self, project: Any, linear_state_ids: set[str]) -> None:
        from plane.db.models import State  # noqa: delayed import

        queryset = State.objects.filter(
            workspace_id=self.workspace_id,
            project=project,
            external_source=mapper.EXTERNAL_SOURCE,
        )
        if linear_state_ids:
            queryset = queryset.exclude(external_id__in=linear_state_ids)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Pruned {deleted} state record(s) missing from Linear in {project.name}")

    def _prune_missing_issues(self, project: Any, linear_issue_ids: set[str]) -> None:
        from plane.db.models import Issue  # noqa: delayed import

        queryset = Issue.objects.filter(
            workspace_id=self.workspace_id,
            project=project,
            external_source=mapper.EXTERNAL_SOURCE,
        )
        if linear_issue_ids:
            queryset = queryset.exclude(external_id__in=linear_issue_ids)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Pruned {deleted} issue record(s) missing from Linear in {project.name}")

    # ------------------------------------------------------------------
    # Estimates
    # ------------------------------------------------------------------

    def _setup_estimates(
        self, linear_issues: list[dict[str, Any]], project: Any
    ) -> dict[float, Any]:
        """Create an Estimate + EstimatePoints for a project based on the
        unique estimate values found across its issues.  Returns a mapping of
        ``{numeric_value: estimate_point_pk}``.
        """
        from plane.db.models import Estimate, EstimatePoint  # noqa: delayed

        values = sorted(
            {i["estimate"] for i in linear_issues if i.get("estimate") is not None}
        )
        if not values:
            return {}

        estimate_name, estimate_type, estimate_points, linear_value_map = resolve_linear_estimate_scale(
            set(values), self.estimate_scale
        )

        try:
            estimate, _ = Estimate.objects.update_or_create(
                workspace_id=self.workspace_id,
                project=project,
                name=estimate_name,
                defaults={
                    "description": "Imported from Linear",
                    "type": estimate_type,
                    "last_used": True,
                    "created_by_id": self.owner_id,
                    "updated_by_id": self.owner_id,
                },
            )
            if project.estimate_id != estimate.pk:
                project.estimate = estimate
                project.updated_by_id = self.owner_id
                project.save(update_fields=["estimate", "updated_by", "updated_at"])
        except Exception as exc:
            msg = f"Estimate setup for project {project}: {exc}"
            logger.error(msg)
            self.stats.errors.append(msg)
            return {}

        point_map: dict[float, Any] = {}
        for key, display_value in estimate_points:
            try:
                ep, created = EstimatePoint.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    estimate=estimate,
                    key=key,
                    defaults={
                        "value": display_value,
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                for linear_value, mapped_key in linear_value_map.items():
                    if mapped_key == key:
                        point_map[linear_value] = ep.pk
                if created:
                    self.stats.estimates += 1
            except Exception as exc:
                msg = f"EstimatePoint {display_value}: {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        return point_map

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def _import_issues(
        self,
        linear_issues: list[dict[str, Any]],
        project: Any,
        state_map: dict[str, Any],
        estimate_point_map: dict[float, Any] | None = None,
    ) -> None:
        """Import issues in two passes: create first, then set parent links."""
        from plane.db.models import (  # noqa: delayed
            Issue,
            IssueAssignee,
            IssueLabel,
            IssueSequence,
        )

        estimate_point_map = estimate_point_map or {}

        if self.dry_run:
            self.stats.issues += len(linear_issues)
            return

        # Seed issue map from existing imported issues so relation mapping can
        # resolve references even when resume mode skips issue reprocessing.
        for ext_id, pk in Issue.objects.filter(
            workspace_id=self.workspace_id,
            project=project,
            external_source=mapper.EXTERNAL_SOURCE,
        ).values_list("external_id", "pk"):
            self._issue_map[str(ext_id)] = pk

        # --- Pass 1: create issues without parent links ---
        total_issues = len(linear_issues)
        for issue_index, li in enumerate(linear_issues, start=1):
            if (
                self.resume_completed
                and self.checkpoint_store
                and self.checkpoint_store.is_issue_done(li["id"])
            ):
                self.stats.skipped += 1
                continue

            if issue_index == 1 or issue_index % 25 == 0 or issue_index == total_issues:
                self._progress(
                    f"Importing issue {issue_index}/{total_issues} in {project.name}: {li.get('identifier', li['id'])}"
                )

            mapped = mapper.map_issue(
                li,
                state_map=state_map,
                label_map=self._label_map,
                user_map=self._user_map,
            )
            fields = mapped["fields"]
            fields.pop("parent_id", None)  # defer parents to pass 2

            # Wire estimate point if available
            est_val = mapped.get("estimate_value")
            if est_val is not None and est_val in estimate_point_map:
                fields["estimate_point_id"] = estimate_point_map[est_val]
            elif self.authoritative_sync:
                fields["estimate_point_id"] = None

            try:
                issue, created = Issue.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    external_source=mapper.EXTERNAL_SOURCE,
                    external_id=li["id"],
                    defaults={
                        **fields,
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                self._issue_map[li["id"]] = issue.pk

                if created:
                    # Create sequence entry
                    last_seq = (
                        IssueSequence.objects.filter(
                            project=project
                        ).order_by("-sequence").values_list(
                            "sequence", flat=True
                        ).first()
                        or 0
                    )
                    IssueSequence.objects.create(
                        issue=issue,
                        sequence=last_seq + 1,
                        project=project,
                        workspace_id=self.workspace_id,
                        created_by_id=self.owner_id,
                        updated_by_id=self.owner_id,
                    )
                    issue.sequence_id = last_seq + 1
                    issue.save(update_fields=["sequence_id"])
                    self.stats.issues += 1
                else:
                    self.stats.skipped += 1

                # Assignee (through-model)
                if mapped["assignee_pk"]:
                    IssueAssignee.objects.get_or_create(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue=issue,
                        assignee_id=mapped["assignee_pk"],
                        defaults={
                            "created_by_id": self.owner_id,
                            "updated_by_id": self.owner_id,
                        },
                    )

                # Labels (through-model)
                for label_pk in mapped["label_pks"]:
                    IssueLabel.objects.get_or_create(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue=issue,
                        label_id=label_pk,
                        defaults={
                            "created_by_id": self.owner_id,
                            "updated_by_id": self.owner_id,
                        },
                    )

                if self.authoritative_sync:
                    self._reconcile_issue_assignees(issue, project, mapped["assignee_pk"])
                    self._reconcile_issue_labels(issue, project, mapped["label_pks"])

            except Exception as exc:
                msg = f"Issue '{li.get('identifier', li['id'])}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        # --- Pass 2: wire parent links ---
        for li in linear_issues:
            if (
                self.resume_completed
                and self.checkpoint_store
                and self.checkpoint_store.is_issue_done(li["id"])
            ):
                continue
            parent_ref = li.get("parent")
            plane_issue_pk = self._issue_map.get(li["id"])
            if not plane_issue_pk:
                continue
            plane_parent_pk = self._issue_map.get(parent_ref["id"]) if parent_ref else None
            if plane_parent_pk:
                try:
                    Issue.objects.filter(pk=plane_issue_pk).update(
                        parent_id=plane_parent_pk
                    )
                except Exception as exc:
                    msg = f"Parent link {li['id']}->{parent_ref['id']}: {exc}"
                    logger.error(msg)
                    self.stats.errors.append(msg)
            elif self.authoritative_sync:
                try:
                    Issue.objects.filter(pk=plane_issue_pk).update(parent_id=None)
                except Exception as exc:
                    msg = f"Parent clear {li['id']}: {exc}"
                    logger.error(msg)
                    self.stats.errors.append(msg)

        # --- Comments, Attachments, Relations (per issue) ---
        for issue_index, li in enumerate(linear_issues, start=1):
            if (
                self.resume_completed
                and self.checkpoint_store
                and self.checkpoint_store.is_issue_done(li["id"])
            ):
                continue
            plane_issue_pk = self._issue_map.get(li["id"])
            if not plane_issue_pk:
                continue
            if issue_index == 1 or issue_index % 25 == 0 or issue_index == len(linear_issues):
                self._progress(
                    f"Fetching comments/attachments/relations for issue {issue_index}/{len(linear_issues)}: {li.get('identifier', li['id'])}"
                )
            self._import_comments(li["id"], plane_issue_pk, project)
            self._import_attachments(li["id"], plane_issue_pk, project)
            self._import_relations(li["id"], plane_issue_pk, project)
            if self.checkpoint_store and not self.dry_run:
                self.checkpoint_store.mark_issue_done(li["id"])

    def _reconcile_issue_assignees(self, issue: Any, project: Any, assignee_pk: Any) -> None:
        from plane.db.models import IssueAssignee  # noqa: delayed import

        queryset = IssueAssignee.objects.filter(
            workspace_id=self.workspace_id,
            project=project,
            issue=issue,
        )
        if assignee_pk:
            queryset = queryset.exclude(assignee_id=assignee_pk)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Reconciled {deleted} stale assignee record(s) for {issue.name}")

    def _reconcile_issue_labels(self, issue: Any, project: Any, label_pks: list[Any]) -> None:
        from plane.db.models import IssueLabel  # noqa: delayed import

        queryset = IssueLabel.objects.filter(
            workspace_id=self.workspace_id,
            project=project,
            issue=issue,
        )
        if label_pks:
            queryset = queryset.exclude(label_id__in=label_pks)
        deleted = self._deleted_count(queryset.delete())
        if deleted > 0:
            self._progress(f"Reconciled {deleted} stale label record(s) for {issue.name}")

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def _import_comments(
        self,
        linear_issue_id: str,
        plane_issue_pk: Any,
        project: Any,
    ) -> None:
        from plane.db.models import IssueComment  # noqa: delayed

        comments = self.client.fetch_comments_for_issue(linear_issue_id)
        imported_comment_ids: set[str] = set()
        for lc in comments:
            imported_comment_ids.add(lc["id"])
            mapped = mapper.map_comment(lc, user_map=self._user_map)
            try:
                _, created = IssueComment.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    issue_id=plane_issue_pk,
                    external_source=mapper.EXTERNAL_SOURCE,
                    external_id=lc["id"],
                    defaults={
                        **mapped,
                        "created_by_id": mapped.get("actor_id") or self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                if created:
                    self.stats.comments += 1
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"Comment {lc['id']} on issue {linear_issue_id}: {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        if self.authoritative_sync:
            queryset = IssueComment.objects.filter(
                workspace_id=self.workspace_id,
                project=project,
                issue_id=plane_issue_pk,
                external_source=mapper.EXTERNAL_SOURCE,
            )
            if imported_comment_ids:
                queryset = queryset.exclude(external_id__in=imported_comment_ids)
            queryset.delete()

    # ------------------------------------------------------------------
    # Attachments → IssueLink
    # ------------------------------------------------------------------

    def _import_attachments(
        self,
        linear_issue_id: str,
        plane_issue_pk: Any,
        project: Any,
    ) -> None:
        from plane.db.models import IssueLink  # noqa: delayed

        attachments = self.client.fetch_attachments_for_issue(linear_issue_id)
        imported_attachment_ids: set[str] = set()
        for att in attachments:
            link_kwargs = mapper.map_attachment_to_link(att)
            if not link_kwargs.get("url"):
                continue
            imported_attachment_ids.add(att["id"])
            try:
                existing = IssueLink.objects.filter(
                    workspace_id=self.workspace_id,
                    project=project,
                    issue_id=plane_issue_pk,
                    metadata__linear_id=link_kwargs["external_id"],
                ).first()
                if existing is None:
                    existing = IssueLink.objects.filter(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue_id=plane_issue_pk,
                        url=link_kwargs["url"],
                    ).first()

                if existing is None:
                    IssueLink.objects.create(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue_id=plane_issue_pk,
                        title=link_kwargs["title"],
                        url=link_kwargs["url"],
                        metadata=link_kwargs["metadata"],
                        created_by_id=self.owner_id,
                        updated_by_id=self.owner_id,
                    )
                    created = True
                else:
                    existing.title = link_kwargs["title"]
                    existing.url = link_kwargs["url"]
                    existing.metadata = link_kwargs["metadata"]
                    existing.updated_by_id = self.owner_id
                    if existing.created_by_id is None:
                        existing.created_by_id = self.owner_id
                    existing.save()
                    created = False
                if created:
                    self.stats.attachments += 1
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"Attachment {att['id']} on issue {linear_issue_id}: {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        if self.authoritative_sync:
            queryset = IssueLink.objects.filter(
                workspace_id=self.workspace_id,
                project=project,
                issue_id=plane_issue_pk,
                metadata__linear_id__isnull=False,
            )
            if imported_attachment_ids:
                queryset = queryset.exclude(metadata__linear_id__in=imported_attachment_ids)
            queryset.delete()

    # ------------------------------------------------------------------
    # Issue Relations (per issue)
    # ------------------------------------------------------------------

    def _import_relations(
        self,
        linear_issue_id: str,
        plane_issue_pk: Any,
        project: Any,
    ) -> None:
        from plane.db.models import IssueRelation  # noqa: delayed
        from django.db.models import Q  # noqa: delayed

        try:
            relations = self.client.fetch_relations_for_issue(linear_issue_id)
        except Exception as exc:
            logger.debug("Could not fetch relations for %s: %s", linear_issue_id, exc)
            return

        desired_relations: set[tuple[Any, Any, str]] = set()
        for rel in relations:
            mapped = self._canonical_relation_tuple(
                rel,
                current_linear_issue_id=linear_issue_id,
            )
            if mapped is None:
                continue

            desired_relations.add(mapped)

            try:
                _, created = IssueRelation.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    issue_id=mapped[0],
                    related_issue_id=mapped[1],
                    defaults={
                        "relation_type": mapped[2],
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                if created:
                    self.stats.relations += 1
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"Relation {rel.get('id', '?')}: {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        if self.authoritative_sync:
            existing_relations = IssueRelation.objects.filter(
                workspace_id=self.workspace_id,
                project=project,
                issue__external_source=mapper.EXTERNAL_SOURCE,
                related_issue__external_source=mapper.EXTERNAL_SOURCE,
            ).filter(Q(issue_id=plane_issue_pk) | Q(related_issue_id=plane_issue_pk))
            for existing in existing_relations:
                relation_key = (
                    existing.issue_id,
                    existing.related_issue_id,
                    existing.relation_type,
                )
                if relation_key not in desired_relations:
                    existing.delete()
