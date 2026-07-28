import json
from datetime import timedelta
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from django.utils import timezone

from modules.accounts.models import User
from modules.dataset_inspection.archive import inspect_shapefile_archive
from modules.dataset_inspection.exceptions import (
    InspectionAuthorizationError,
    InspectionInputError,
    UnsafeArchiveError,
    UnsupportedDatasetFormat,
)
from modules.dataset_inspection.inputs import resolve_completed_upload
from modules.dataset_inspection.types import inspection_job_for_filename
from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job
from modules.uploads.models import UploadSession, UploadStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _completed_upload(user: User, filename: str = "roads.gpkg") -> UploadSession:
    return UploadSession.objects.create(
        created_by=user,
        original_filename=filename,
        content_type="application/octet-stream",
        declared_size=128,
        checksum_sha256="",
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{uuid4()}/source",
        multipart_upload_id="multipart",
        status=UploadStatus.COMPLETED,
        part_size=5 * 1024 * 1024,
        part_count=1,
        completed_parts=[{"PartNumber": 1, "ETag": '"etag"'}],
        actual_size=128,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )


def test_inspection_job_type_selection() -> None:
    assert inspection_job_for_filename("roads.GPKG").job_type == "vector-inspect"
    assert inspection_job_for_filename("roads.zip").queue == "vector"
    assert inspection_job_for_filename("imagery.tiff").job_type == "raster-inspect"
    assert inspection_job_for_filename("imagery.tif").queue == "raster"
    with pytest.raises(UnsupportedDatasetFormat):
        inspection_job_for_filename("notes.csv")


def test_shapefile_archive_validation_accepts_required_sidecars(tmp_path) -> None:
    archive_path = tmp_path / "roads.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("roads/roads.shp", b"shp")
        archive.writestr("roads/roads.dbf", b"dbf")
        archive.writestr("roads/roads.shx", b"shx")

    result = inspect_shapefile_archive(archive_path)

    assert result.shapefile_count == 1
    assert result.member_count == 3
    assert result.uncompressed_size == 9
    assert result.warnings == ("Shapefile roads/roads.shp has no .prj coordinate reference file",)


def test_shapefile_archive_rejects_path_traversal(tmp_path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("../roads.shp", b"shp")
        archive.writestr("../roads.dbf", b"dbf")
        archive.writestr("../roads.shx", b"shx")

    with pytest.raises(UnsafeArchiveError, match="Unsafe archive member path"):
        inspect_shapefile_archive(archive_path)


def test_shapefile_archive_requires_dbf_and_shx(tmp_path) -> None:
    archive_path = tmp_path / "incomplete.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("roads.shp", b"shp")

    with pytest.raises(UnsafeArchiveError, match="missing required sidecars"):
        inspect_shapefile_archive(archive_path)


@pytest.mark.django_db
def test_resolve_completed_upload_enforces_owner_and_status() -> None:
    owner = _user("inspection-owner")
    other = _user("inspection-other")
    upload = _completed_upload(owner)
    owner_job = Job.objects.create(
        created_by=owner,
        job_type="vector-inspect",
        input_parameters={"upload_id": str(upload.id)},
    )
    other_job = Job.objects.create(
        created_by=other,
        job_type="vector-inspect",
        input_parameters={"upload_id": str(upload.id)},
    )

    resolved = resolve_completed_upload(
        JobExecutionContext(owner_job.id),
        owner_job.input_parameters,
    )
    assert resolved.id == upload.id

    with pytest.raises(InspectionAuthorizationError):
        resolve_completed_upload(
            JobExecutionContext(other_job.id),
            other_job.input_parameters,
        )

    upload.status = UploadStatus.FAILED
    upload.save(update_fields=("status", "updated_at"))
    with pytest.raises(InspectionInputError, match="COMPLETED"):
        resolve_completed_upload(
            JobExecutionContext(owner_job.id),
            owner_job.input_parameters,
        )


@pytest.mark.django_db
def test_upload_inspection_endpoint_dispatches_vector_job(client, monkeypatch) -> None:
    owner = _user("inspection-api-owner")
    upload = _completed_upload(owner, filename="network.geojson")

    def fake_dispatch(**kwargs):
        return Job.objects.create(
            created_by=kwargs["created_by"],
            job_type=kwargs["job_type"],
            queue=kwargs["queue"],
            resource=kwargs["resource"],
            input_parameters=kwargs["input_parameters"],
            max_retries=kwargs["max_retries"],
        )

    monkeypatch.setattr("modules.uploads.api.create_and_dispatch_job", fake_dispatch)
    client.force_login(owner)

    response = client.post(
        f"/api/v1/uploads/{upload.id}/inspect",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job_type"] == "vector-inspect"
    assert payload["queue"] == "vector"
    job = Job.objects.get(pk=payload["id"])
    assert job.input_parameters == {"upload_id": str(upload.id)}
    assert job.max_retries == 1


@pytest.mark.django_db
def test_upload_inspection_endpoint_rejects_unfinished_upload(client) -> None:
    owner = _user("inspection-api-unfinished")
    upload = _completed_upload(owner, filename="imagery.tif")
    upload.status = UploadStatus.UPLOADING
    upload.save(update_fields=("status", "updated_at"))
    client.force_login(owner)

    response = client.post(
        f"/api/v1/uploads/{upload.id}/inspect",
        data=json.dumps({}),
        content_type="application/json",
    )

    assert response.status_code == 409
