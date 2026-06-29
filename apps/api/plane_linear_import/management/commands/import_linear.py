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

    def handle(self, **options):
        from plane.db.models import Workspace

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
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN mode — no DB writes."))

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
            )
            stats = importer.run()

        self.stdout.write("\n" + stats.summary())

        if stats.errors:
            self.stdout.write(
                self.style.ERROR(f"\nCompleted with {len(stats.errors)} error(s).")
            )
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("\nImport completed successfully."))
