import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.gis.geos import Polygon
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.models import (
    Dataset,
    DatasetKind,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    VectorDataset,
    VectorLayer,
    VectorLayerStatus,
)
from modules.jobs.models import Job, JobStatus
from modules.permissions.models import (
    PermissionAction,
    PermissionSubjectType,
    ResourcePermission,
)
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.uploads.models import UploadSession, UploadStatus
from modules.vector_exports.models import (
    VectorExport,
    VectorExportFormat,
    VectorExportStatus,
)


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _ready_layer(
    owner: User,
    *,
    visibility: str = Visibility.PRIVATE,
) -> VectorLayer:
    token = uuid4().hex
    resource = Resource.objects.create(
        owner=owner,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Exportable roads",
        slug=f"exportable-roads-{token}",
        visibility=visibility,
        lifecycle_status=LifecycleStatus.READY,
    )
    upload = UploadSession.objects.create(
        created_by=owner,
        resource=resource,
        original_filename="roads.geojson",
        content_type="application/geo+json",
        declared_size=100,
        bucket="geoportalx",
        object_key=f"uploads/{owner.id}/{token}/roads.geojson",
        multipart_upload_id=f"upload-{token}",
        status=UploadStatus.COMPLETED,
        part_size=100,
        part_count=1,
        actual_size=100,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )
    inspection_job = Job.objects.create(
        created_by=owner,
        resource=resource,
        job_type="vector-inspect",
        status=JobStatus.SUCCEEDED,
        progress=100,
        result_payload={},
    )
    dataset = Dataset.objects.create(
        resource=resource,
        kind=DatasetKind.VECTOR,
        status=DatasetStatus.READY,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_upload=upload,
        inspection_job=inspection_job,
        status=DatasetVersionStatus.READY,
        source_format="GeoJSON",
        source_checksum_sha256="",
        inspection_result={},
        created_by=owner,
        imported_at=timezone.now(),
    )
    dataset.current_version = version
    dataset.save(update_fields=("current_version", "updated_at"))
    vector = VectorDataset.objects.create(
        dataset=dataset,
        layer_count=1,
        imported_layer_count=1,
    )
    extent = Polygon.from_bbox((118.0, 31.0, 119.0, 33.0))
    extent.srid = 4326
    return VectorLayer.objects.create(
        vector_dataset=vector,
        version=version,
        ordinal=1,
        source_layer_name="roads",
        title="Roads",
        status=VectorLayerStatus.READY,
        field_schema=[
            {
                "name": "gx_fid",
                "data_type": "integer",
                "database_type": "int4",
                "nullable": False,
            },
            {
                "name": "name",
                "data_type": "text",
                "database_type": "text",
                "nullable": True,
            },
            {
                "name": "speed",
                "data_type": "integer",
                "database_type": "int4",
                "nullable": True,
            },
        ],
        geometry_type="MULTILINESTRING",
        geometry_column="geom",
        srid=4326,
        feature_count=2,
        db_schema="geoportalx_data",
        db_table=f"v_{token}",
        tile_source_id=f"v_{token}",
        extent=extent,
    )


@pytest.mark.django_db

def test_owner_can_create_bounded_vector_export(client, monkeypatch) -> None:
    owner = _user("export-owner")
    layer = _ready_layer(owner)
    monkeypatch.setattr("modules.jobs.services.dispatch_job", lambda job_id: None)
    client.force_login(owner)

    response = client.post(
        "/api/v1/vector-exports/",
        data=json.dumps(
            {
                "layer_id": str(layer.id),
                "export_format": VectorExportFormat.GEOJSON,
                "fields": ["name"],
                "bbox": [118.0, 31.0, 119.0, 33.0],
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 202
    body = response.json()
    assert body["format"] == VectorExportFormat.GEOJSON
    assert body["status"] == VectorExportStatus.PENDING
    export = VectorExport.objects.get(pk=body["id"])
    assert export.selected_fields == ["name"]
    assert export.job is not None
    assert export.job.queue == "vector"


@pytest.mark.django_db

def test_public_view_does_not_imply_download_permission(client, monkeypatch) -> None:
    owner = _user("public-export-owner")
    reader = _user("public-export-reader")
    layer = _ready_layer(owner, visibility=Visibility.PUBLIC)
    monkeypatch.setattr("modules.jobs.services.dispatch_job", lambda job_id: None)
    client.force_login(reader)

    denied = client.post(
        "/api/v1/vector-exports/",
        data=json.dumps(
            {
                "layer_id": str(layer.id),
                "export_format": VectorExportFormat.CSV,
            }
        ),
        content_type="application/json",
    )
    assert denied.status_code == 404

    ResourcePermission.objects.create(
        resource=layer.vector_dataset.dataset.resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=reader.id,
        action=PermissionAction.DOWNLOAD,
        granted_by=owner,
    )
    allowed = client.post(
        "/api/v1/vector-exports/",
        data=json.dumps(
            {
                "layer_id": str(layer.id),
                "export_format": VectorExportFormat.CSV,
            }
        ),
        content_type="application/json",
    )
    assert allowed.status_code == 202


@pytest.mark.django_db

def test_download_is_signed_only_while_permission_is_current(client, monkeypatch) -> None:
    owner = _user("signed-export-owner")
    downloader = _user("signed-export-downloader")
    layer = _ready_layer(owner, visibility=Visibility.PUBLIC)
    grant = ResourcePermission.objects.create(
        resource=layer.vector_dataset.dataset.resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=downloader.id,
        action=PermissionAction.DOWNLOAD,
        granted_by=owner,
    )
    export = VectorExport.objects.create(
        layer=layer,
        created_by=downloader,
        export_format=VectorExportFormat.GEOPACKAGE,
        status=VectorExportStatus.READY,
        bucket="geoportalx",
        object_key=f"exports/{downloader.id}/{uuid4()}/result.gpkg",
        content_type="application/geopackage+sqlite3",
        result_filename="roads.gpkg",
        result_size=1024,
        checksum_sha256="a" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )
    monkeypatch.setattr(
        "modules.vector_exports.services.presign_download",
        lambda **kwargs: "http://minio.local/signed-download",
    )
    client.force_login(downloader)

    response = client.get(f"/api/v1/vector-exports/{export.id}/download")
    assert response.status_code == 200
    assert response.json()["url"] == "http://minio.local/signed-download"

    grant.delete()
    denied = client.get(f"/api/v1/vector-exports/{export.id}/download")
    assert denied.status_code == 404


@pytest.mark.django_db

def test_export_cancel_synchronizes_queued_job(client) -> None:
    owner = _user("cancel-export-owner")
    layer = _ready_layer(owner)
    job = Job.objects.create(
        created_by=owner,
        resource=layer.vector_dataset.dataset.resource,
        job_type="vector-export",
        status=JobStatus.PENDING,
    )
    export = VectorExport.objects.create(
        layer=layer,
        created_by=owner,
        job=job,
        export_format=VectorExportFormat.GEOJSON,
    )
    client.force_login(owner)

    response = client.post(f"/api/v1/vector-exports/{export.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == VectorExportStatus.CANCELLED
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
