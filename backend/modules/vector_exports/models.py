import uuid

from django.conf import settings
from django.db import models


class VectorExportFormat(models.TextChoices):
    GEOJSON = "GEOJSON", "GeoJSON"
    CSV = "CSV", "CSV"
    GEOPACKAGE = "GEOPACKAGE", "GeoPackage"


class VectorExportStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    RUNNING = "RUNNING", "Running"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"
    EXPIRED = "EXPIRED", "Expired"


class VectorExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer = models.ForeignKey(
        "datasets.VectorLayer",
        on_delete=models.CASCADE,
        related_name="exports",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vector_exports",
    )
    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.PROTECT,
        related_name="vector_export",
        null=True,
        blank=True,
    )
    export_format = models.CharField(max_length=16, choices=VectorExportFormat.choices)
    status = models.CharField(
        max_length=16,
        choices=VectorExportStatus.choices,
        default=VectorExportStatus.PENDING,
        db_index=True,
    )
    selected_fields = models.JSONField(default=list, blank=True)
    bbox = models.JSONField(default=list, blank=True)
    bucket = models.CharField(max_length=255, blank=True)
    object_key = models.CharField(max_length=1024, blank=True)
    object_version_id = models.CharField(max_length=255, blank=True)
    object_etag = models.CharField(max_length=255, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    result_filename = models.CharField(max_length=255, blank=True)
    result_size = models.PositiveBigIntegerField(default=0)
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("created_by", "status"),
                name="vec_export_owner_status",
            ),
            models.Index(
                fields=("status", "expires_at"),
                name="vec_export_status_expiry",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.layer}: {self.export_format} ({self.status})"
