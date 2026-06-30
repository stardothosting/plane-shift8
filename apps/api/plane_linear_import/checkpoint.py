"""Checkpoint persistence for resumable Linear imports."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ImportCheckpointStore:
    """Stores completed teams/issues in a local JSON file."""

    def __init__(
        self,
        file_path: str,
        *,
        enabled: bool = False,
        workspace_key: str | None = None,
        reset: bool = False,
    ):
        self.enabled = enabled
        self.path = Path(file_path)
        self.workspace_key = workspace_key or ""

        self._data: dict[str, Any] = {
            "version": 1,
            "workspace_key": self.workspace_key,
            "updated_at": "",
            "completed_teams": [],
            "completed_issues": [],
        }

        if not self.enabled:
            return

        if reset:
            self._persist()
            return

        self._load()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_json(self) -> dict[str, Any] | None:
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("Could not read checkpoint file %s: %s", self.path, exc)
            return None
        return loaded if isinstance(loaded, dict) else None

    def _workspace_mismatch(self, loaded: dict[str, Any]) -> bool:
        loaded_workspace = loaded.get("workspace_key") or ""
        return bool(
            loaded_workspace
            and self.workspace_key
            and loaded_workspace != self.workspace_key
        )

    def _apply_loaded_data(self, loaded: dict[str, Any]) -> None:
        loaded_workspace = loaded.get("workspace_key") or ""
        self._data["workspace_key"] = self.workspace_key or loaded_workspace
        self._data["completed_teams"] = list(loaded.get("completed_teams") or [])
        self._data["completed_issues"] = list(loaded.get("completed_issues") or [])
        self._data["updated_at"] = loaded.get("updated_at") or self._now()
        self._data["last_sync_at"] = loaded.get("last_sync_at") or ""

    def _load(self) -> None:
        if not self.path.exists():
            self._persist()
            return

        loaded = self._load_json()
        if loaded is None:
            self._persist()
            return

        if self._workspace_mismatch(loaded):
            logger.info(
                "Checkpoint workspace key changed (%s -> %s), resetting checkpoint",
                loaded.get("workspace_key") or "",
                self.workspace_key,
            )
            self._persist()
            return

        self._apply_loaded_data(loaded)

    def _persist(self) -> None:
        if not self.enabled:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data["workspace_key"] = self.workspace_key
        self._data["updated_at"] = self._now()
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")

    def is_team_done(self, linear_team_id: str) -> bool:
        return self.enabled and linear_team_id in set(self._data["completed_teams"])

    def mark_team_done(self, linear_team_id: str) -> None:
        if not self.enabled:
            return
        teams = set(self._data["completed_teams"])
        if linear_team_id in teams:
            return
        teams.add(linear_team_id)
        self._data["completed_teams"] = sorted(teams)
        self._persist()

    def is_issue_done(self, linear_issue_id: str) -> bool:
        return self.enabled and linear_issue_id in set(self._data["completed_issues"])

    def mark_issue_done(self, linear_issue_id: str) -> None:
        if not self.enabled:
            return
        issues = set(self._data["completed_issues"])
        if linear_issue_id in issues:
            return
        issues.add(linear_issue_id)
        self._data["completed_issues"] = sorted(issues)
        self._persist()

    def get_last_sync_at(self) -> datetime | None:
        """Return the UTC datetime of the last completed sync, or None."""
        value = self._data.get("last_sync_at") or ""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def mark_sync_complete(self, sync_started_at: datetime) -> None:
        """Record that a sync run completed, storing the time it started.

        We store *start* time (not end time) so issues updated during the sync
        window are picked up on the next run.
        """
        self._data["last_sync_at"] = sync_started_at.isoformat()
        self._persist()
