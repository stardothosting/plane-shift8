"""Main orchestration — pull from Linear, push into Plane's Django models.

Designed to be called from a Django management command where the ORM is
already initialised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import entity_mapper as mapper
from .linear_client import LinearClient

logger = logging.getLogger(__name__)


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
    reactions: int = 0
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
            f"Reactions      : {self.reactions}",
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
        dry_run: bool = False,
    ):
        self.client = client
        self.workspace_id = workspace_id
        self.owner_id = owner_id
        self.team_ids = team_ids
        self.dry_run = dry_run
        self.stats = ImportStats()

        # Lookup maps: Linear ID → Plane PK
        self._state_map: dict[str, Any] = {}
        self._label_map: dict[str, Any] = {}
        self._user_map: dict[str, Any] = {}
        self._issue_map: dict[str, Any] = {}  # linear issue id → plane issue pk

        # Deferred data: collected during issue import, processed after.
        # Each entry: (linear_issue_dict, project)
        self._all_linear_issues: list[tuple[dict[str, Any], Any]] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> ImportStats:
        """Execute the full import and return statistics."""
        logger.info("Starting Linear import (dry_run=%s)", self.dry_run)

        self._import_users()
        self._import_labels()

        teams = self.client.fetch_teams()
        if self.team_ids:
            teams = [t for t in teams if t["id"] in self.team_ids]

        for team in teams:
            self._import_team(team)

        # Deferred passes (need all issues across all teams in _issue_map)
        self._import_all_relations()
        self._import_all_reactions()

        logger.info("Import finished.\n%s", self.stats.summary())
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

    def _import_labels(self) -> None:
        """Import workspace-level labels."""
        from plane.db.models import Label  # noqa: delayed import

        linear_labels = self.client.fetch_labels()
        for ll in linear_labels:
            kwargs = mapper.map_label(ll)
            if self.dry_run:
                self.stats.labels += 1
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
            except Exception as exc:
                msg = f"Label '{ll.get('name')}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

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
        for ls in team.get("states", {}).get("nodes", []):
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

        # Issues (two passes: create, then wire parents)
        issues = self.client.fetch_issues_for_team(team["id"])

        # Build estimate system for this project before importing issues
        estimate_point_map: dict[float, Any] = {}
        if not self.dry_run and project:
            estimate_point_map = self._setup_estimates(issues, project)

        self._import_issues(issues, project, team_state_map, estimate_point_map)

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

        try:
            estimate, _ = Estimate.objects.update_or_create(
                workspace_id=self.workspace_id,
                project=project,
                name="Linear Estimates",
                defaults={
                    "description": "Imported from Linear",
                    "type": "points",
                    "last_used": True,
                    "created_by_id": self.owner_id,
                    "updated_by_id": self.owner_id,
                },
            )
        except Exception as exc:
            msg = f"Estimate setup for project {project}: {exc}"
            logger.error(msg)
            self.stats.errors.append(msg)
            return {}

        point_map: dict[float, Any] = {}
        for idx, val in enumerate(values):
            try:
                ep, created = EstimatePoint.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    estimate=estimate,
                    key=idx,
                    defaults={
                        "value": str(val),
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                point_map[val] = ep.pk
                if created:
                    self.stats.estimates += 1
            except Exception as exc:
                msg = f"EstimatePoint {val}: {exc}"
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

        # --- Pass 1: create issues without parent links ---
        for li in linear_issues:
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

                # Stash for deferred passes (relations, reactions, attachments)
                self._all_linear_issues.append((li, project))

            except Exception as exc:
                msg = f"Issue '{li.get('identifier', li['id'])}': {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

        # --- Pass 2: wire parent links ---
        for li in linear_issues:
            parent_ref = li.get("parent")
            if not parent_ref:
                continue
            plane_issue_pk = self._issue_map.get(li["id"])
            plane_parent_pk = self._issue_map.get(parent_ref["id"])
            if plane_issue_pk and plane_parent_pk:
                try:
                    Issue.objects.filter(pk=plane_issue_pk).update(
                        parent_id=plane_parent_pk
                    )
                except Exception as exc:
                    msg = f"Parent link {li['id']}->{parent_ref['id']}: {exc}"
                    logger.error(msg)
                    self.stats.errors.append(msg)

        # --- Comments + Attachments (per issue) ---
        for li in linear_issues:
            plane_issue_pk = self._issue_map.get(li["id"])
            if not plane_issue_pk:
                continue
            self._import_comments(li["id"], plane_issue_pk, project)
            self._import_attachments(li["id"], plane_issue_pk, project)

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
        for lc in comments:
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
        for att in attachments:
            link_kwargs = mapper.map_attachment_to_link(att)
            if not link_kwargs.get("url"):
                continue
            try:
                _, created = IssueLink.objects.update_or_create(
                    workspace_id=self.workspace_id,
                    project=project,
                    issue_id=plane_issue_pk,
                    url=link_kwargs["url"],
                    defaults={
                        "title": link_kwargs["title"],
                        "metadata": link_kwargs["metadata"],
                        "created_by_id": self.owner_id,
                        "updated_by_id": self.owner_id,
                    },
                )
                if created:
                    self.stats.attachments += 1
                else:
                    self.stats.skipped += 1
            except Exception as exc:
                msg = f"Attachment {att['id']} on issue {linear_issue_id}: {exc}"
                logger.error(msg)
                self.stats.errors.append(msg)

    # ------------------------------------------------------------------
    # Issue Relations (deferred — needs full _issue_map)
    # ------------------------------------------------------------------

    def _import_all_relations(self) -> None:
        """Process relations from all issues after all teams have been imported."""
        from plane.db.models import IssueRelation  # noqa: delayed

        if self.dry_run:
            return

        seen_pairs: set[tuple[Any, Any]] = set()

        for li, project in self._all_linear_issues:
            relations = (li.get("relations") or {}).get("nodes", [])
            for rel in relations:
                mapped = mapper.map_issue_relation(
                    rel,
                    issue_map=self._issue_map,
                    current_linear_issue_id=li["id"],
                )
                if mapped is None:
                    continue

                # Deduplicate symmetric pairs (duplicate, relates_to)
                pair = (mapped["issue_id"], mapped["related_issue_id"])
                reverse_pair = (mapped["related_issue_id"], mapped["issue_id"])
                if pair in seen_pairs or reverse_pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                try:
                    _, created = IssueRelation.objects.get_or_create(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue_id=mapped["issue_id"],
                        related_issue_id=mapped["related_issue_id"],
                        defaults={
                            "relation_type": mapped["relation_type"],
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

    # ------------------------------------------------------------------
    # Reactions (deferred — needs full _issue_map)
    # ------------------------------------------------------------------

    def _import_all_reactions(self) -> None:
        """Import emoji reactions from all issues."""
        from plane.db.models import IssueReaction  # noqa: delayed

        if self.dry_run:
            return

        for li, project in self._all_linear_issues:
            plane_issue_pk = self._issue_map.get(li["id"])
            if not plane_issue_pk:
                continue

            reactions = (li.get("reactions") or {}).get("nodes", [])
            for rxn in reactions:
                mapped = mapper.map_reaction(rxn, user_map=self._user_map)
                if mapped is None:
                    continue
                try:
                    _, created = IssueReaction.objects.get_or_create(
                        workspace_id=self.workspace_id,
                        project=project,
                        issue_id=plane_issue_pk,
                        actor_id=mapped["actor_id"],
                        reaction=mapped["reaction"],
                        defaults={
                            "created_by_id": self.owner_id,
                            "updated_by_id": self.owner_id,
                        },
                    )
                    if created:
                        self.stats.reactions += 1
                    else:
                        self.stats.skipped += 1
                except Exception as exc:
                    msg = f"Reaction on issue {li['id']}: {exc}"
                    logger.error(msg)
                    self.stats.errors.append(msg)
