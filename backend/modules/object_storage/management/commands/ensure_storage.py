from django.core.management.base import BaseCommand, CommandError

from modules.object_storage.services import ObjectStorageError, ensure_bucket


class Command(BaseCommand):
    help = "Create and configure the canonical S3-compatible storage bucket."

    def handle(self, *args, **options):
        try:
            ensure_bucket()
        except ObjectStorageError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Object storage is ready"))
