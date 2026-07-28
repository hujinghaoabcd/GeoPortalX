from django.db import transaction
from django.utils import timezone

from .models import Job, JobStatus

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.PENDING: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


@transaction.atomic
def transition_job(
    *,
    job: Job,
    target_status: str,
    progress: int | None = None,
    error_code: str = "",
    error_message: str = "",
) -> Job:
    """Apply a validated, row-locked transition to the permanent job ledger."""

    locked = Job.objects.select_for_update().get(pk=job.pk)
    if target_status not in JobStatus.values:
        raise ValueError(f"Unknown job status: {target_status}")
    if target_status not in _ALLOWED_TRANSITIONS[locked.status]:
        raise ValueError(f"Invalid job transition: {locked.status} -> {target_status}")
    if progress is not None and not 0 <= progress <= 100:
        raise ValueError("Job progress must be between 0 and 100")

    now = timezone.now()
    locked.status = target_status

    if target_status == JobStatus.RUNNING and locked.started_at is None:
        locked.started_at = now
    if target_status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        locked.finished_at = now
    if target_status == JobStatus.SUCCEEDED:
        locked.progress = 100
    elif progress is not None:
        locked.progress = progress

    if target_status == JobStatus.FAILED:
        locked.error_code = error_code
        locked.error_message = error_message
    else:
        locked.error_code = ""
        locked.error_message = ""

    locked.save(
        update_fields=(
            "status",
            "progress",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def mark_job_queued(*, job: Job, celery_task_id: str) -> Job:
    """Persist the broker task identifier and move a pending job to queued."""

    locked = Job.objects.select_for_update().get(pk=job.pk)
    if locked.status != JobStatus.PENDING:
        raise ValueError(f"Only pending jobs can be queued, got {locked.status}")

    locked.celery_task_id = celery_task_id
    locked.status = JobStatus.QUEUED
    locked.save(update_fields=("celery_task_id", "status", "updated_at"))
    return locked
