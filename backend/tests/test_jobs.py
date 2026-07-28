import pytest

from modules.accounts.models import User
from modules.jobs.models import Job, JobStatus
from modules.jobs.services import (
    JobCancellationRequested,
    acknowledge_job_cancellation,
    dispatch_job,
    mark_job_queued,
    mark_job_retrying,
    report_job_progress,
    request_job_cancellation,
    transition_job,
)
from modules.jobs.tasks import execute_job


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


@pytest.mark.django_db
def test_job_lifecycle_reaches_success() -> None:
    user = _user("job-owner")
    job = Job.objects.create(created_by=user, job_type="health-probe")

    job = mark_job_queued(job=job, celery_task_id="task-1")
    assert job.status == JobStatus.QUEUED

    job = transition_job(job=job, target_status=JobStatus.RUNNING, progress=10)
    assert job.started_at is not None
    assert job.progress == 10

    job = transition_job(
        job=job,
        target_status=JobStatus.SUCCEEDED,
        result_payload={"status": "ok"},
    )
    assert job.status == JobStatus.SUCCEEDED
    assert job.progress == 100
    assert job.result_payload == {"status": "ok"}
    assert job.finished_at is not None


@pytest.mark.django_db
def test_invalid_job_transition_is_rejected() -> None:
    user = _user("invalid-job-owner")
    job = Job.objects.create(created_by=user, job_type="invalid-transition")

    with pytest.raises(ValueError, match="Invalid job transition"):
        transition_job(job=job, target_status=JobStatus.SUCCEEDED)


@pytest.mark.django_db
def test_failed_job_records_error_details() -> None:
    user = _user("failed-job-owner")
    job = Job.objects.create(
        created_by=user,
        job_type="failing-task",
        status=JobStatus.QUEUED,
    )

    job = transition_job(
        job=job,
        target_status=JobStatus.FAILED,
        error_code="IMPORT_FAILED",
        error_message="The input dataset could not be imported",
    )

    assert job.error_code == "IMPORT_FAILED"
    assert job.error_message == "The input dataset could not be imported"


@pytest.mark.django_db
def test_progress_is_monotonic_and_running_cancellation_is_cooperative() -> None:
    user = _user("progress-owner")
    job = Job.objects.create(
        created_by=user,
        job_type="health-probe",
        status=JobStatus.RUNNING,
        progress=5,
    )

    job = report_job_progress(job_id=job.id, progress=25, message="Working")
    assert job.progress == 25
    assert job.last_heartbeat_at is not None

    with pytest.raises(ValueError, match="monotonic"):
        report_job_progress(job_id=job.id, progress=20)

    job = request_job_cancellation(job=job, requested_by=user)
    assert job.status == JobStatus.RUNNING
    assert job.cancellation_requested_at is not None

    with pytest.raises(JobCancellationRequested):
        report_job_progress(job_id=job.id, progress=50)

    job = acknowledge_job_cancellation(job_id=job.id)
    assert job.status == JobStatus.CANCELLED
    assert job.finished_at is not None


@pytest.mark.django_db
def test_pending_job_cancels_before_broker_dispatch() -> None:
    user = _user("pending-cancel-owner")
    job = Job.objects.create(created_by=user, job_type="health-probe")

    job = request_job_cancellation(job=job, requested_by=user)

    assert job.status == JobStatus.CANCELLED
    assert job.progress_message == "Cancelled before execution"


@pytest.mark.django_db
def test_retrying_state_increments_persistent_counter() -> None:
    user = _user("retry-owner")
    job = Job.objects.create(
        created_by=user,
        job_type="health-probe",
        status=JobStatus.RUNNING,
        max_retries=2,
    )

    job = mark_job_retrying(job_id=job.id, error_message="Temporary failure")

    assert job.status == JobStatus.RETRYING
    assert job.retry_count == 1
    assert job.error_code == "RETRYABLE_ERROR"


@pytest.mark.django_db
def test_dispatch_persists_task_identifier(monkeypatch) -> None:
    user = _user("dispatch-owner")
    job = Job.objects.create(created_by=user, job_type="health-probe")
    dispatched: dict = {}

    def fake_apply_async(*, args, task_id, queue, priority):
        dispatched.update(
            args=args,
            task_id=task_id,
            queue=queue,
            priority=priority,
        )

    monkeypatch.setattr(execute_job, "apply_async", fake_apply_async)
    job = dispatch_job(job.id)

    assert job.status == JobStatus.QUEUED
    assert job.celery_task_id == dispatched["task_id"]
    assert dispatched["args"] == [str(job.id)]


@pytest.mark.django_db
def test_health_probe_task_persists_progress_and_result() -> None:
    user = _user("worker-owner")
    job = Job.objects.create(
        created_by=user,
        job_type="health-probe",
        status=JobStatus.QUEUED,
        input_parameters={"probe": "worker"},
    )

    result = execute_job.run(str(job.id))
    job.refresh_from_db()

    assert result == {"status": "ok", "echo": {"probe": "worker"}}
    assert job.status == JobStatus.SUCCEEDED
    assert job.progress == 100
    assert job.result_payload == result
    assert job.last_heartbeat_at is not None
