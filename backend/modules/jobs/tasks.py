from uuid import UUID

from celery import shared_task

from .context import JobExecutionContext
from .exceptions import RetryableJobError
from .models import Job, JobStatus
from .registry import get_job_handler
from .services import (
    JobCancellationRequested,
    acknowledge_job_cancellation,
    mark_job_retrying,
    transition_job,
)


@shared_task(
    bind=True,
    name="modules.jobs.tasks.execute_job",
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_job(self, job_id: str) -> dict:
    """Execute one registered job while PostgreSQL remains the source of truth."""

    job_uuid = UUID(job_id)
    job = Job.objects.get(pk=job_uuid)

    if job.status == JobStatus.SUCCEEDED:
        return job.result_payload
    if job.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
        return {"status": job.status.lower()}
    if job.status == JobStatus.RUNNING:
        return {"status": "already-running"}
    if job.status not in {JobStatus.QUEUED, JobStatus.RETRYING}:
        return {"status": "not-ready", "job_status": job.status}

    if job.cancellation_requested_at is not None:
        cancelled = acknowledge_job_cancellation(job_id=job_uuid)
        return {"status": cancelled.status.lower()}

    job = transition_job(
        job=job,
        target_status=JobStatus.RUNNING,
        progress=max(job.progress, 1),
        progress_message="Running",
    )
    context = JobExecutionContext(job_id=job_uuid)

    try:
        handler = get_job_handler(job.job_type)
        result = handler(context, dict(job.input_parameters)) or {}
        context.ensure_not_cancelled()
    except JobCancellationRequested:
        cancelled = acknowledge_job_cancellation(job_id=job_uuid)
        return {"status": cancelled.status.lower()}
    except RetryableJobError as exc:
        current = Job.objects.get(pk=job_uuid)
        if current.retry_count >= current.max_retries:
            transition_job(
                job=current,
                target_status=JobStatus.FAILED,
                error_code="JOB_RETRY_LIMIT_EXCEEDED",
                error_message=str(exc),
                progress_message="Retry limit exceeded",
            )
            raise

        retrying = mark_job_retrying(job_id=job_uuid, error_message=str(exc))
        countdown = min(2**retrying.retry_count, 60)
        raise self.retry(
            exc=exc,
            countdown=countdown,
            max_retries=retrying.max_retries,
        ) from exc
    except Exception as exc:
        transition_job(
            job=Job.objects.get(pk=job_uuid),
            target_status=JobStatus.FAILED,
            error_code=exc.__class__.__name__.upper()[:100],
            error_message=str(exc),
            progress_message="Failed",
        )
        raise

    completed = transition_job(
        job=Job.objects.get(pk=job_uuid),
        target_status=JobStatus.SUCCEEDED,
        result_payload=result,
        progress_message="Completed",
    )
    return completed.result_payload
