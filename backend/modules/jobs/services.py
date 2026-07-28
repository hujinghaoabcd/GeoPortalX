from functools import partial
from typing import Any
from uuid import UUID, uuid4

from celery import current_app
from django.db import transaction
from django.utils import timezone

from modules.accounts.models import User
from modules.resources.models import Resource

from .models import Job, JobStatus
from .registry import is_registered_job_type


class JobCancellationRequested(Exception):
    """Raised inside workers when a cooperative cancellation was requested."""


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.PENDING: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.RETRYING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.RETRYING: {
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}
_TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


@transaction.atomic
def create_and_dispatch_job(
    *,
    created_by: User,
    job_type: str,
    input_parameters: dict[str, Any] | None = None,
    resource: Resource | None = None,
    queue: str = "system",
    priority: int = 0,
    max_retries: int = 3,
) -> Job:
    """Create the permanent ledger row and dispatch only after commit."""

    if not is_registered_job_type(job_type):
        raise ValueError(f"Unknown job type: {job_type}")
    if not queue or len(queue) > 50:
        raise ValueError("Job queue must be between 1 and 50 characters")
    if not -32768 <= priority <= 32767:
        raise ValueError("Job priority is outside the supported range")
    if not 0 <= max_retries <= 20:
        raise ValueError("Job max_retries must be between 0 and 20")

    job = Job.objects.create(
        created_by=created_by,
        job_type=job_type,
        input_parameters=input_parameters or {},
        resource=resource,
        queue=queue,
        priority=priority,
        max_retries=max_retries,
    )
    transaction.on_commit(partial(dispatch_job, job.id))
    return job


def dispatch_job(job_id: UUID) -> Job:
    """Queue a committed job and persist broker-delivery failures."""

    from .tasks import execute_job

    job = Job.objects.get(pk=job_id)
    task_id = str(uuid4())
    job = mark_job_queued(job=job, celery_task_id=task_id)
    try:
        execute_job.apply_async(
            args=[str(job.id)],
            task_id=task_id,
            queue=job.queue,
            priority=job.priority,
        )
    except Exception as exc:  # pragma: no cover - broker-specific failures
        job = transition_job(
            job=job,
            target_status=JobStatus.FAILED,
            error_code="JOB_DISPATCH_FAILED",
            error_message=str(exc),
            progress_message="Could not deliver the task to the broker",
        )
    return job


@transaction.atomic
def transition_job(
    *,
    job: Job,
    target_status: str,
    progress: int | None = None,
    progress_message: str | None = None,
    error_code: str = "",
    error_message: str = "",
    result_payload: dict[str, Any] | None = None,
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
    locked.last_heartbeat_at = now

    if target_status == JobStatus.RUNNING and locked.started_at is None:
        locked.started_at = now
    if target_status in _TERMINAL_STATUSES:
        locked.finished_at = now
    if target_status == JobStatus.SUCCEEDED:
        locked.progress = 100
        locked.progress_message = progress_message or "Completed"
        locked.result_payload = result_payload or {}
    elif progress is not None:
        locked.progress = progress
    if progress_message is not None and target_status != JobStatus.SUCCEEDED:
        locked.progress_message = progress_message

    if target_status == JobStatus.FAILED:
        locked.error_code = error_code
        locked.error_message = error_message
    elif target_status != JobStatus.RETRYING:
        locked.error_code = ""
        locked.error_message = ""

    locked.save(
        update_fields=(
            "status",
            "progress",
            "progress_message",
            "result_payload",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "last_heartbeat_at",
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
    locked.progress_message = "Queued"
    locked.save(
        update_fields=(
            "celery_task_id",
            "status",
            "progress_message",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def report_job_progress(
    *,
    job_id: UUID,
    progress: int,
    message: str = "",
) -> Job:
    """Persist monotonic progress and check cooperative cancellation."""

    locked = Job.objects.select_for_update().get(pk=job_id)
    if locked.status != JobStatus.RUNNING:
        raise ValueError(f"Only running jobs can report progress, got {locked.status}")
    if locked.cancellation_requested_at is not None:
        raise JobCancellationRequested(str(job_id))
    if not locked.progress <= progress <= 99:
        raise ValueError("Job progress must be monotonic and between current progress and 99")

    locked.progress = progress
    locked.progress_message = message
    locked.last_heartbeat_at = timezone.now()
    locked.save(
        update_fields=(
            "progress",
            "progress_message",
            "last_heartbeat_at",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def mark_job_retrying(*, job_id: UUID, error_message: str) -> Job:
    """Persist one retry attempt before Celery republishes the task message."""

    locked = Job.objects.select_for_update().get(pk=job_id)
    if locked.status != JobStatus.RUNNING:
        raise ValueError(f"Only running jobs can retry, got {locked.status}")
    if locked.cancellation_requested_at is not None:
        raise JobCancellationRequested(str(job_id))
    if locked.retry_count >= locked.max_retries:
        raise ValueError("Job retry limit has been reached")

    locked.status = JobStatus.RETRYING
    locked.retry_count += 1
    locked.error_code = "RETRYABLE_ERROR"
    locked.error_message = error_message
    locked.progress_message = f"Retrying ({locked.retry_count}/{locked.max_retries})"
    locked.last_heartbeat_at = timezone.now()
    locked.save(
        update_fields=(
            "status",
            "retry_count",
            "error_code",
            "error_message",
            "progress_message",
            "last_heartbeat_at",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def request_job_cancellation(*, job: Job, requested_by: User) -> Job:
    """Request cancellation and immediately finish tasks that are not running."""

    locked = Job.objects.select_for_update().get(pk=job.pk)
    if locked.created_by_id != requested_by.id and not requested_by.is_superuser:
        raise PermissionError("Only the job creator can cancel this job")
    if locked.status in _TERMINAL_STATUSES:
        return locked

    now = timezone.now()
    locked.cancellation_requested_at = locked.cancellation_requested_at or now
    update_fields = ["cancellation_requested_at", "updated_at"]

    if locked.status in {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RETRYING}:
        locked.status = JobStatus.CANCELLED
        locked.finished_at = now
        locked.progress_message = "Cancelled before execution"
        update_fields.extend(("status", "finished_at", "progress_message"))
    else:
        locked.progress_message = "Cancellation requested"
        update_fields.append("progress_message")

    locked.save(update_fields=tuple(update_fields))
    if locked.celery_task_id:
        transaction.on_commit(partial(_revoke_task, locked.celery_task_id))
    return locked


@transaction.atomic
def acknowledge_job_cancellation(*, job_id: UUID) -> Job:
    """Let a running worker acknowledge a cooperative cancellation request."""

    locked = Job.objects.select_for_update().get(pk=job_id)
    if locked.status == JobStatus.CANCELLED:
        return locked
    if locked.status not in {JobStatus.RUNNING, JobStatus.RETRYING}:
        raise ValueError(f"Job cannot acknowledge cancellation from {locked.status}")
    if locked.cancellation_requested_at is None:
        raise ValueError("Job has no cancellation request")

    locked.status = JobStatus.CANCELLED
    locked.finished_at = timezone.now()
    locked.progress_message = "Cancelled"
    locked.last_heartbeat_at = locked.finished_at
    locked.save(
        update_fields=(
            "status",
            "finished_at",
            "progress_message",
            "last_heartbeat_at",
            "updated_at",
        )
    )
    return locked


def ensure_job_not_cancelled(*, job_id: UUID) -> None:
    cancellation_requested = Job.objects.filter(
        pk=job_id,
        cancellation_requested_at__isnull=False,
    ).exists()
    if cancellation_requested:
        raise JobCancellationRequested(str(job_id))


def _revoke_task(task_id: str) -> None:
    current_app.control.revoke(task_id, terminate=False)
