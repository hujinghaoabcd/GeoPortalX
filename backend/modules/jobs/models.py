import uuid

from django.conf import settings
from django.db import models


class JobStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    job_type = models.CharField(max_length=100, db_index=True)
    queue = models.CharField(max_length=50, default="system", db_index=True)
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.PENDING, db_index=True)
    priority = models.SmallIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    input_parameters = models.JSONField(default=dict, blank=True)
    output_resources = models.JSONField(default=list, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=models.Q(progress__gte=0, progress__lte=100), name="job_progress_range"),
        ]

    def __str__(self) -> str:
        return f"{self.job_type}: {self.status}"
