import pytest

from modules.accounts.models import User
from modules.jobs.models import Job, JobStatus
from modules.jobs.services import mark_job_queued, transition_job


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

    job = transition_job(job=job, target_status=JobStatus.SUCCEEDED)
    assert job.status == JobStatus.SUCCEEDED
    assert job.progress == 100
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
