import uuid

from django.conf import settings
from django.db import models


class UploadStatus(models.TextChoices):
    INITIATING = "INITIATING", "Initiating"
    UPLOADING = "UPLOADING", "Uploading"
    COMPLETING = "COMPLETING", "Completing"
    ABORTING = "ABORTING", "Aborting"
    COMPLETED = "COMPLETED", "Completed"
    ABORTED = "ABORTED", "Aborted"
    FAILED = "FAILED", "Failed"
    EXPIRED = "EXPIRED", "Expired"


class UploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="upload_sessions",
    )
    resource = models.ForeignKey(
        "resources.Resource",
        on_delete=models.SET_NULL,
        related_name="upload_sessions",
        null=True,
        blank=True,
    )
    original_filename = models.CharField(max_length=512)
    content_type = models.CharField(max_length=255, default="application/octet-stream")
    declared_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    bucket = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024, unique=True)
    multipart_upload_id = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=UploadStatus.choices,
        default=UploadStatus.INITIATING,
        db_index=True,
    )
    part_size = models.PositiveBigIntegerField()
    part_count = models.PositiveIntegerField()
    completed_parts = models.JSONField(default=list, blank=True)
    actual_size = models.PositiveBigIntegerField(null=True, blank=True)
    object_etag = models.CharField(max_length=255, blank=True)
    object_version_id = models.CharField(max_length=255, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("created_by", "status"), name="upload_owner_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(declared_size__gt=0),
                name="upload_declared_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(part_count__gte=1, part_count__lte=10000),
                name="upload_part_count_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename}: {self.status}"
