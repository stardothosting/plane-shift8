"""Map Linear entities to Plane model kwargs.

All functions are pure — they accept raw dicts from the Linear API and return
dicts suitable for passing to ``Model.objects.create(**kwargs)`` or
``Model.objects.update_or_create(defaults=…)``.

No Django imports are needed here, keeping the module testable in isolation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ------------------------------------------------------------------ #
# Priority mapping
# ------------------------------------------------------------------ #
# Linear: 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low
# Plane:  "none", "urgent", "high", "medium", "low"
_LINEAR_PRIORITY_TO_PLANE: dict[int, str] = {
    0: "none",
    1: "urgent",
    2: "high",
    3: "medium",
    4: "low",
}

# ------------------------------------------------------------------ #
# State-group mapping
# ------------------------------------------------------------------ #
# Linear state types → Plane state groups
# Linear types: backlog, unstarted, started, completed, canceled, triage
_LINEAR_STATE_TYPE_TO_PLANE_GROUP: dict[str, str] = {
    "backlog": "backlog",
    "unstarted": "unstarted",
    "started": "started",
    "completed": "completed",
    "canceled": "cancelled",  # note spelling difference
    "cancelled": "cancelled",
    "triage": "triage",
}

# ------------------------------------------------------------------ #
# Issue relation type mapping
# ------------------------------------------------------------------ #
# Linear relation types → (Plane type, swap_direction?)
# "swap_direction" means the Plane record should have issue/related_issue reversed.
# e.g. Linear "blocks": A blocks B → Plane: B is "blocked_by" A (swap)
_LINEAR_RELATION_MAP: dict[str, tuple[str, bool]] = {
    "blocks": ("blocked_by", True),
    "duplicate": ("duplicate", False),
    "related": ("relates_to", False),
}
# "blocked_by" is the inverse of "blocks" — skip to avoid duplicate records.
_SKIP_RELATION_TYPES = {"blocked_by"}

EXTERNAL_SOURCE = "linear"


def _clean_identifier(key: str) -> str:
    """Turn a Linear team key into a valid Plane project identifier (≤12 chars, uppercase alpha)."""
    cleaned = re.sub(r"[^A-Za-z]", "", key).upper()
    return cleaned[:12] if cleaned else "LIN"


def map_team_to_project(
    team: dict[str, Any],
) -> dict[str, Any]:
    """Return kwargs for creating a Plane Project from a Linear Team."""
    return {
        "name": team["name"],
        "description": team.get("description") or "",
        "identifier": _clean_identifier(team["key"]),
        "network": 0,  # Secret by default
        "module_view": False,
        "cycle_view": False,
        "page_view": False,
        "external_source": EXTERNAL_SOURCE,
        "external_id": team["id"],
    }


def map_state(
    linear_state: dict[str, Any],
    *,
    sequence_base: float = 15000.0,
) -> dict[str, Any]:
    """Return kwargs for creating a Plane State from a Linear workflow state."""
    state_type = (linear_state.get("type") or "backlog").lower()
    group = _LINEAR_STATE_TYPE_TO_PLANE_GROUP.get(state_type, "backlog")
    position = linear_state.get("position") or 0
    return {
        "name": linear_state["name"],
        "color": linear_state.get("color") or "#60646C",
        "group": group,
        "sequence": sequence_base + position,
        "external_source": EXTERNAL_SOURCE,
        "external_id": linear_state["id"],
    }


def map_label(label: dict[str, Any]) -> dict[str, Any]:
    """Return kwargs for creating a Plane Label from a Linear IssueLabel."""
    return {
        "name": label["name"],
        "color": label.get("color") or "",
        "external_source": EXTERNAL_SOURCE,
        "external_id": label["id"],
    }


def map_issue(
    issue: dict[str, Any],
    *,
    state_map: dict[str, Any] | None = None,
    label_map: dict[str, Any] | None = None,
    user_map: dict[str, Any] | None = None,
    parent_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return kwargs for creating a Plane Issue from a Linear Issue.

    Lookup maps translate Linear UUIDs to Plane model PKs:
    - *state_map*: ``{linear_state_id: plane_state_pk}``
    - *label_map*: ``{linear_label_id: plane_label_pk}``
    - *user_map*: ``{linear_user_id: plane_user_pk}``
    - *parent_map*: ``{linear_issue_id: plane_issue_pk}``
    """
    state_map = state_map or {}
    label_map = label_map or {}
    user_map = user_map or {}
    parent_map = parent_map or {}

    priority = _LINEAR_PRIORITY_TO_PLANE.get(issue.get("priority", 0), "none")

    # Map state
    state_id = None
    linear_state = issue.get("state")
    if linear_state and linear_state.get("id") in state_map:
        state_id = state_map[linear_state["id"]]

    # Map parent
    parent_id = None
    linear_parent = issue.get("parent")
    if linear_parent and linear_parent.get("id") in parent_map:
        parent_id = parent_map[linear_parent["id"]]

    # Description — Linear returns Markdown; Plane wants HTML.
    # We store the raw markdown in description_html wrapped in a <p> for now.
    # A richer conversion can be added later.
    raw_desc = issue.get("description") or ""
    desc_html = f"<p>{_escape_html(raw_desc)}</p>" if raw_desc else "<p></p>"

    kwargs: dict[str, Any] = {
        "name": issue["title"],
        "description_html": desc_html,
        "description_stripped": raw_desc,
        "priority": priority,
        "start_date": _parse_date(issue.get("startedAt")) or _parse_date(issue.get("dueDate")),
        "target_date": _parse_date(issue.get("dueDate")),
        "external_source": EXTERNAL_SOURCE,
        "external_id": issue["id"],
    }

    if state_id is not None:
        kwargs["state_id"] = state_id
    if parent_id is not None:
        kwargs["parent_id"] = parent_id

    # Preserve original timestamps from Linear
    created_at = _parse_datetime(issue.get("createdAt"))
    if created_at:
        kwargs["created_at"] = created_at
    updated_at = _parse_datetime(issue.get("updatedAt"))
    if updated_at:
        kwargs["updated_at"] = updated_at
    completed_at = _parse_datetime(issue.get("completedAt"))
    if completed_at:
        kwargs["completed_at"] = completed_at

    # Assignee and label IDs returned separately for M2M handling
    assignee_pk = None
    assignee = issue.get("assignee")
    if assignee and assignee.get("id") in user_map:
        assignee_pk = user_map[assignee["id"]]

    label_pks: list[Any] = []
    for lbl in (issue.get("labels") or {}).get("nodes", []):
        pk = label_map.get(lbl["id"])
        if pk is not None:
            label_pks.append(pk)

    # Estimate value (numeric) — caller handles EstimatePoint creation
    estimate_value = issue.get("estimate")

    return {
        "fields": kwargs,
        "assignee_pk": assignee_pk,
        "label_pks": label_pks,
        "estimate_value": estimate_value,
    }


def map_comment(
    comment: dict[str, Any],
    *,
    user_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return kwargs for creating a Plane IssueComment from a Linear Comment."""
    user_map = user_map or {}
    body = comment.get("body") or ""

    actor_id = None
    user = comment.get("user")
    if user and user.get("id") in user_map:
        actor_id = user_map[user["id"]]

    kwargs: dict[str, Any] = {
        "comment_html": f"<p>{_escape_html(body)}</p>" if body else "<p></p>",
        "comment_stripped": body,
        "external_source": EXTERNAL_SOURCE,
        "external_id": comment["id"],
        "actor_id": actor_id,
    }

    # Preserve original timestamps
    created_at = _parse_datetime(comment.get("createdAt"))
    if created_at:
        kwargs["created_at"] = created_at
    updated_at = _parse_datetime(comment.get("updatedAt"))
    if updated_at:
        kwargs["updated_at"] = updated_at

    return kwargs


def map_attachment_to_link(
    attachment: dict[str, Any],
) -> dict[str, Any]:
    """Return kwargs for creating a Plane IssueLink from a Linear Attachment."""
    title = attachment.get("title") or attachment.get("subtitle") or "Linear attachment"
    url = attachment.get("url") or ""
    source_info = attachment.get("source") or {}
    metadata = {
        "linear_id": attachment["id"],
        "source_type": source_info.get("type"),
    }
    return {
        "title": title[:255],
        "url": url,
        "metadata": metadata,
        "external_source": EXTERNAL_SOURCE,
        "external_id": attachment["id"],
    }


def map_issue_relation(
    relation: dict[str, Any],
    *,
    issue_map: dict[str, Any],
    current_linear_issue_id: str,
) -> dict[str, Any] | None:
    """Return kwargs for creating a Plane IssueRelation from a Linear IssueRelation.

    Returns ``None`` if the relation type should be skipped (inverse direction)
    or the related issue hasn't been imported.
    """
    rel_type = (relation.get("type") or "").lower()

    # Skip inverse types to avoid duplicate records
    if rel_type in _SKIP_RELATION_TYPES:
        return None

    mapping = _LINEAR_RELATION_MAP.get(rel_type)
    if mapping is None:
        return None

    plane_type, swap = mapping
    related_issue = relation.get("relatedIssue") or {}
    related_linear_id = related_issue.get("id")
    if not related_linear_id:
        return None

    # Both issues must have been imported
    plane_issue_pk = issue_map.get(current_linear_issue_id)
    plane_related_pk = issue_map.get(related_linear_id)
    if not plane_issue_pk or not plane_related_pk:
        return None

    if swap:
        issue_pk, related_pk = plane_related_pk, plane_issue_pk
    else:
        issue_pk, related_pk = plane_issue_pk, plane_related_pk

    return {
        "issue_id": issue_pk,
        "related_issue_id": related_pk,
        "relation_type": plane_type,
    }


def map_reaction(
    reaction: dict[str, Any],
    *,
    user_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return kwargs for creating a Plane IssueReaction from a Linear Reaction.

    Returns ``None`` if the reacting user isn't mapped to a Plane account.
    """
    user_map = user_map or {}
    emoji = reaction.get("emoji") or ""
    if not emoji:
        return None

    user = reaction.get("user") or {}
    user_id = user.get("id")
    actor_pk = user_map.get(user_id) if user_id else None
    if not actor_pk:
        return None

    return {
        "actor_id": actor_pk,
        "reaction": emoji,
    }


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _escape_html(text: str) -> str:
    """Minimal HTML-entity escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br/>")
    )


def _parse_date(value: str | None) -> str | None:
    """Return a YYYY-MM-DD string or ``None``."""
    if not value:
        return None
    # Linear dates arrive as ISO-8601; take just the date part.
    return value[:10] if len(value) >= 10 else value


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string into a timezone-aware datetime, or ``None``."""
    if not value:
        return None
    try:
        # Linear sends: "2024-03-15T10:30:00.000Z"
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None
