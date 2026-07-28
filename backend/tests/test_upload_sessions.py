import json

import pytest

from modules.accounts.models import User
from modules.object_storage.services import StoredObject
from modules.uploads import services as upload_services
from modules.uploads.models import UploadStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


@pytest.mark.django_db
def test_upload_session_uses_safe_generated_object_key(monkeypatch) -> None:
    user = _user("uploader")
    monkeypatch.setattr(
        upload_services.storage,
        "initiate_multipart_upload",
        lambda **kwargs: "multipart-1",
    )

    session = upload_services.create_upload_session(
        created_by=user,
        original_filename="../../roads.GPKG",
        declared_size=1024,
        content_type="application/geopackage+sqlite3",
    )

    assert session.status == UploadStatus.UPLOADING
    assert session.multipart_upload_id == "multipart-1"
    assert session.object_key.startswith(f"uploads/{user.id}/{session.id}/")
    assert session.object_key.endswith("source.gpkg")
    assert ".." not in session.object_key
    assert session.part_count == 1


@pytest.mark.django_db
def test_upload_completion_verifies_stored_size(monkeypatch) -> None:
    user = _user("complete-owner")
    monkeypatch.setattr(
        upload_services.storage,
        "initiate_multipart_upload",
        lambda **kwargs: "multipart-2",
    )
    monkeypatch.setattr(
        upload_services.storage,
        "complete_multipart_upload",
        lambda **kwargs: {"ETag": '"etag-1"'},
    )
    monkeypatch.setattr(
        upload_services.storage,
        "inspect_object",
        lambda **kwargs: StoredObject(
            bucket="geoportalx",
            key=kwargs["key"],
            size=2048,
            etag="etag-1",
            version_id="",
            content_type="application/octet-stream",
        ),
    )

    session = upload_services.create_upload_session(
        created_by=user,
        original_filename="roads.zip",
        declared_size=2048,
    )
    completed = upload_services.complete_upload_session(
        session=session,
        parts=[{"PartNumber": 1, "ETag": '"part-etag"'}],
    )

    assert completed.status == UploadStatus.COMPLETED
    assert completed.actual_size == 2048
    assert completed.object_etag == "etag-1"
    assert completed.completed_parts == [{"PartNumber": 1, "ETag": '"part-etag"'}]


@pytest.mark.django_db
def test_upload_completion_rejects_missing_parts(monkeypatch) -> None:
    user = _user("missing-parts-owner")
    monkeypatch.setattr(
        upload_services.storage,
        "initiate_multipart_upload",
        lambda **kwargs: "multipart-3",
    )
    session = upload_services.create_upload_session(
        created_by=user,
        original_filename="large.tif",
        declared_size=100 * 1024 * 1024,
    )

    with pytest.raises(ValueError, match="All upload parts"):
        upload_services.complete_upload_session(session=session, parts=[])


@pytest.mark.django_db
def test_upload_abort_marks_session_aborted(monkeypatch) -> None:
    user = _user("abort-owner")
    monkeypatch.setattr(
        upload_services.storage,
        "initiate_multipart_upload",
        lambda **kwargs: "multipart-4",
    )
    monkeypatch.setattr(
        upload_services.storage,
        "abort_multipart_upload",
        lambda **kwargs: None,
    )
    session = upload_services.create_upload_session(
        created_by=user,
        original_filename="cancelled.geojson",
        declared_size=512,
    )

    aborted = upload_services.abort_upload_session(session=session)

    assert aborted.status == UploadStatus.ABORTED
    assert aborted.aborted_at is not None


@pytest.mark.django_db
def test_upload_api_isolates_sessions_by_owner(client, monkeypatch) -> None:
    owner = _user("api-upload-owner")
    other = _user("api-upload-other")
    monkeypatch.setattr(
        upload_services.storage,
        "initiate_multipart_upload",
        lambda **kwargs: "multipart-api",
    )

    client.force_login(owner)
    response = client.post(
        "/api/v1/uploads/",
        data=json.dumps(
            {
                "original_filename": "network.gpkg",
                "declared_size": 4096,
                "content_type": "application/geopackage+sqlite3",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201
    upload_id = response.json()["id"]

    client.force_login(other)
    hidden = client.get(f"/api/v1/uploads/{upload_id}")
    assert hidden.status_code == 404
