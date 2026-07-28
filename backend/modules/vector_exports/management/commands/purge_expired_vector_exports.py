from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from modules.object_storage.services import ObjectStorageError, delete_object
from modules.vector_exports.models import VectorExport, VectorExportStatus


class Command(BaseCommand):
    help = "Delete expired vector-export objects and retain their audit metadata."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of export objects to inspect in one run.",
        )

    def handle(self, *args, **options) -> None:
        limit = int(options["limit"])
        if limit <= 0:
            raise ValueError("limit must be positive")

        now = timezone.now()
        queryset = (
            VectorExport.objects.filter(object_key__gt="")
            .filter(
                Q(status=VectorExportStatus.EXPIRED)
                | Q(
                    status=VectorExportStatus.READY,
                    expires_at__isnull=False,
                    expires_at__lte=now,
                )
            )
            .order_by("expires_at", "created_at")[:limit]
        )
        purged = 0
        failed = 0
        for export in queryset:
            try:
                delete_object(key=export.object_key)
            except ObjectStorageError as exc:
                failed += 1
                self.stderr.write(f"Could not purge {export.id}: {exc}")
                continue

            export.status = VectorExportStatus.EXPIRED
            export.bucket = ""
            export.object_key = ""
            export.object_version_id = ""
            export.object_etag = ""
            export.save(
                update_fields=(
                    "status",
                    "bucket",
                    "object_key",
                    "object_version_id",
                    "object_etag",
                    "updated_at",
                )
            )
            purged += 1

        self.stdout.write(
            self.style.SUCCESS(f"Purged {purged} vector exports; {failed} failed")
        )
