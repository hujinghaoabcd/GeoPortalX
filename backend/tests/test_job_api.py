import json

import pytest

from modules.accounts.models import User
from modules.jobs.models import Job, JobStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


@pytest.mark.django_db
def test_job_api_requires_authentication(client) -> None:
    response = client.get("/api/v1/jobs/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_job_type_catalog_lists_registered_handlers(client) -> None:
    user = _user("type-user")
    client.force_login(user)

    response = client.get("/api/v1/jobs/types")

    assert response.status_code == 200
    assert "health-probe" in response.json()["job_types"]


@pytest.mark.django_db
def test_user_can_create_list_and_cancel_pending_job(client) -> None:
    user = _user("job-api-owner")
    client.force_login(user)

    response = client.post(
        "/api/v1/jobs/",
        data=json.dumps(
            {
                "job_type": "health-probe",
                "input_parameters": {"probe": "api"},
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == JobStatus.PENDING
    assert payload["input_parameters"] == {"probe": "api"}

    listing = client.get("/api/v1/jobs/")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [payload["id"]]

    cancelled = client.post(f"/api/v1/jobs/{payload['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == JobStatus.CANCELLED


@pytest.mark.django_db
def test_unknown_job_type_is_rejected(client) -> None:
    user = _user("unknown-type-user")
    client.force_login(user)

    response = client.post(
        "/api/v1/jobs/",
        data=json.dumps({"job_type": "unknown-handler"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "Unknown job type" in response.json()["detail"]


@pytest.mark.django_db
def test_users_cannot_read_each_others_jobs(client) -> None:
    owner = _user("private-job-owner")
    viewer = _user("private-job-viewer")
    job = Job.objects.create(created_by=owner, job_type="health-probe")
    client.force_login(viewer)

    listing = client.get("/api/v1/jobs/")
    detail = client.get(f"/api/v1/jobs/{job.id}")

    assert listing.status_code == 200
    assert listing.json() == []
    assert detail.status_code == 404
