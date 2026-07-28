import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from modules.datasets.reconciliation import reconcile_vector_imports


class Command(BaseCommand):
    help = (
        "Inventory managed PostGIS vector tables and import Jobs. "
        "The command is dry-run unless --apply is supplied."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--stale-after-minutes",
            type=int,
            default=60,
            help="Grace period before an import artifact is considered stale.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Allow explicitly selected cleanup actions to mutate state.",
        )
        parser.add_argument(
            "--drop-orphans",
            action="store_true",
            help="Drop only managed orphan/stale tables classified as safe.",
        )
        parser.add_argument(
            "--fail-stale-versions",
            action="store_true",
            help="Mark stale importing versions failed after removing their managed tables.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Write the complete reconciliation report as JSON.",
        )
        parser.add_argument(
            "--fail-on-critical",
            action="store_true",
            help="Exit with an error after reporting if critical issues remain.",
        )

    def handle(self, *args, **options) -> None:
        stale_minutes = int(options["stale_after_minutes"])
        apply = bool(options["apply"])
        drop_orphans = bool(options["drop_orphans"])
        fail_stale_versions = bool(options["fail_stale_versions"])
        if stale_minutes <= 0:
            raise CommandError("--stale-after-minutes must be positive")
        if not apply and (drop_orphans or fail_stale_versions):
            raise CommandError("--drop-orphans and --fail-stale-versions require --apply")
        if apply and not (drop_orphans or fail_stale_versions):
            raise CommandError("--apply requires at least one explicit cleanup option")

        report = reconcile_vector_imports(
            stale_after=timedelta(minutes=stale_minutes),
            apply=apply,
            drop_orphans=drop_orphans,
            fail_stale_versions=fail_stale_versions,
        )
        payload = report.as_dict()
        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self._write_human_report(payload, dry_run=not apply)

        critical_count = payload["severity_counts"].get("CRITICAL", 0)
        if options["fail_on_critical"] and critical_count:
            raise CommandError(f"Reconciliation found {critical_count} critical issue(s)")

    def _write_human_report(self, payload: dict, *, dry_run: bool) -> None:
        mode = "DRY RUN" if dry_run else "APPLY"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Vector import reconciliation: {mode}"))
        self.stdout.write(
            "Scanned "
            f"{payload['tables_scanned']} tables "
            f"({payload['managed_tables_scanned']} managed); "
            f"protected {payload['protected_table_count']}."
        )
        self.stdout.write(
            f"Found {payload['issue_count']} issue(s): {payload['severity_counts']}"
        )
        for issue in payload["issues"]:
            target = ".".join(
                value for value in (issue["schema"], issue["table"]) if value
            )
            identifiers = [
                value
                for value in (
                    f"table={target}" if target else "",
                    f"version={issue['version_id']}" if issue["version_id"] else "",
                    f"layer={issue['layer_id']}" if issue["layer_id"] else "",
                    f"job={issue['job_id']}" if issue["job_id"] else "",
                )
                if value
            ]
            suffix = f" ({', '.join(identifiers)})" if identifiers else ""
            self.stdout.write(
                f"[{issue['severity']}] {issue['code']}: {issue['summary']}{suffix}"
            )
        if payload["applied_actions"]:
            self.stdout.write(self.style.SUCCESS("Applied actions:"))
            for action in payload["applied_actions"]:
                self.stdout.write(f"- {action}")
        elif dry_run:
            self.stdout.write(
                "No changes were made. Use --apply with an explicit cleanup option."
            )
