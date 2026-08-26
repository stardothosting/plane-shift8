"""
Django management command: import_linear

Usage:
    python manage.py import_linear --workspace-slug <slug> [options]

Required settings (in Django settings or environment):
    LINEAR_API_KEY  — A Linear personal API key.

Options:
    --workspace-slug   Plane workspace slug (required)
    --team-ids         Comma-separated Linear team IDs to import (optional; all if omitted)
    --dry-run          Fetch from Linear but skip all DB writes
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Import data from Linear into a Plane workspace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace-slug",
            default="",
            help="Slug of the target Plane workspace (auto-detected if only one exists).",
        )
        parser.add_argument(
            "--team-ids",
            default="",
            help="Comma-separated list of Linear team IDs to import (default: all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Fetch from Linear but do not write to the database.",
        )
        parser.add_argument(
            "--api-key",
            default="",
            help="Linear API key (falls back to LINEAR_API_KEY env var).",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            default=False,
            help="Resume from checkpoint file and skip completed teams/issues.",
        )
        parser.add_argument(
            "--checkpoint-file",
            default=".linear-import-checkpoint.json",
            help="Path to checkpoint JSON file used with --resume.",
        )
        parser.add_argument(
            "--reset-checkpoint",
            action="store_true",
            default=False,
            help="Reset checkpoint file before import (only with --resume).",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            default=False,
            help=(
                "Differential sync: only fetch issues updated since the last "
                "successful sync (stored in checkpoint file). "
                "Falls back to a full import if no prior sync is recorded. "
                "Requires --checkpoint-file to be consistent across runs."
            ),
        )

    def handle(self, **options):
        from plane.db.models import Workspace

        from plane_linear_import.checkpoint import ImportCheckpointStore
        from plane_linear_import.importer import LinearImporter
        from plane_linear_import.linear_client import LinearClient

        api_key = options["api_key"] or os.environ.get("LINEAR_API_KEY", "")
        if not api_key:
            raise CommandError(
                "Provide --api-key or set the LINEAR_API_KEY environment variable."
            )

        slug = options["workspace_slug"]
        if slug:
            try:
                workspace = Workspace.objects.get(slug=slug)
            except Workspace.DoesNotExist:
                raise CommandError(f"Workspace with slug '{slug}' not found.")
        else:
            workspaces = list(Workspace.objects.all()[:2])
            if len(workspaces) == 0:
                raise CommandError("No workspaces exist. Create one first.")
            if len(workspaces) > 1:
                slugs = ", ".join(
                    Workspace.objects.values_list("slug", flat=True)
                )
                raise CommandError(
                    f"Multiple workspaces found. Specify --workspace-slug. Available: {slugs}"
                )
            workspace = workspaces[0]
            self.stdout.write(f"Auto-detected workspace: {workspace.name} ({workspace.slug})")

        team_ids = [
            t.strip() for t in options["team_ids"].split(",") if t.strip()
        ] or None

        dry_run = options["dry_run"]
        resume = options["resume"]
        checkpoint_file = options["checkpoint_file"]
        reset_checkpoint = options["reset_checkpoint"]
        sync_mode = options["sync"]

        if reset_checkpoint and not resume:
            raise CommandError("--reset-checkpoint requires --resume.")

        # --sync always needs the checkpoint file to persist last_sync_at;
        # enable it automatically so the user doesn't have to remember --resume.
        if sync_mode:
            resume = True

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN mode — no DB writes."))

        checkpoint_store = ImportCheckpointStore(
            checkpoint_file,
            enabled=resume,
            workspace_key=str(workspace.pk),
            reset=reset_checkpoint,
        )

        # Determine the since-datetime for differential sync.
        since: datetime | None = None
        if sync_mode:
            since = checkpoint_store.get_last_sync_at()
            if since:
                self.stdout.write(
                    f"Sync mode: fetching issues updated since {since.isoformat()}"
                )
            else:
                self.stdout.write(
                    "Sync mode: no previous sync recorded — performing full import"
                )
        elif resume:
            self.stdout.write(
                f"Resume mode enabled (checkpoint: {checkpoint_file})"
            )

        # Record the start time BEFORE fetching so any issues updated during
        # the run are caught by the next sync (start-time semantics).
        sync_started_at = datetime.now(timezone.utc)

        with LinearClient(api_key) as client:
            # Verify connectivity
            try:
                org = client.fetch_organization()
            except Exception as exc:
                raise CommandError(f"Failed to connect to Linear API: {exc}")

            self.stdout.write(
                f"Connected to Linear org: {org['name']} ({org['urlKey']})"
            )

            importer = LinearImporter(
                client=client,
                workspace_id=workspace.pk,
                owner_id=workspace.owner_id,
                team_ids=team_ids,
                dry_run=dry_run,
                checkpoint_store=checkpoint_store,
                progress_callback=self.stdout.write,
                since=since,
                resume_completed=not sync_mode,
            )
            stats = importer.run()

        # Persist last_sync_at on success (even partial — idempotent re-runs are safe).
        if sync_mode and not dry_run and not stats.errors:
            checkpoint_store.mark_sync_complete(sync_started_at)
            self.stdout.write(
                f"Sync complete. Next run will fetch changes after {sync_started_at.isoformat()}"
            )
        elif sync_mode and stats.errors:
            self.stdout.write(
                "Sync checkpoint not advanced because the import recorded errors."
            )

        self.stdout.write("\n" + stats.summary())

        if stats.errors:
            self.stdout.write(
                self.style.ERROR(f"\nCompleted with {len(stats.errors)} error(s).")
            )
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("\nImport completed successfully."))
