import json
from datetime import timedelta

import pytest
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.models import DatasetKind, DatasetStatus, RasterDataset, VectorLayer
from modules.datasets.services import register_dataset_from_inspection
from modules.jobs.models import Job, JobStatus
from modules.resources.models import ResourceType
from modules.uploads.models import UploadSession, UploadStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _completed_upload(user: User, filename: str) -> UploadSession:
    return UploadSession.objects.create(
        created_by=user,
        original_filename=filename,
        content_type="application/octet-stream",
        declared_size=1024,
        checksum_sha256="a" * 64,
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{filename}",
        multipart_upload_id="upload-id",
        status=UploadStatus.COMPLETED,
        part_size=1024,
        part_count=1,
        actual_size=1024,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )


def _inspection_job(user: User, upload: UploadSession, *, raster: bool = False) -> Job:
    inspection = (
        {
            "dataset_type": "raster",
            "format": "GeoTIFF",
            "driver": "GTiff",
            "width": 16,
            "height": 8,
            "band_count": 1,
            "crs": "EPSG:4326",
            "epsg": 4326,
            "bounds": [0, 0, 16, 8],
            "transform": [1, 0, 0, 0, -1, 8],
            "bands": [{"index": 1, "dtype": "uint8"}],
            "image_structure": {"compression": "deflate"},
            "cog_readiness": {"needs_conversion": True},
        }
        if raster
        else {
            "dataset_type": "vector",
            "format": "GeoJSON",
            "layer_count": 2,
            "layers": [
                {
                    "name": "roads",
                    "driver": "GeoJSON",
                    "geometry_type": "LineString",
                    "feature_count": 2,
                    "crs": "EPSG:4326",
                    "bounds": [0, 0, 1, 1],
                    "fields": [{"name": "name", "dtype": "object"}],
                },
                {
                    "name": "metadata",
                    "driver": "GeoJSON",
                    "geometry_type": None,
                    "feature_count": 1,
                    "crs": None,
                    "bounds": None,
                    "fields": [],
                },
            ],
            "warnings": ["Layer metadata is nonspatial"],
        }
    )
    return Job.objects.create(
        created_by=user,
        job_type="raster-inspect" if raster else "vector-inspect",
        status=JobStatus.SUCCEEDED,
        progress=100,
        result_payload={
            "upload": {
                "id": str(upload.id),
                "sha256": "a" * 64,
                "resource_id": None,
            },
            "inspection": inspection,
        },
    )


@pytest.mark.django_db
def test_vector_registration_creates_dataset_version_and_spatial_layers() -> None:
    user = _user("vector-register")
    upload = _completed_upload(user, "roads.geojson")
    inspection_job = _inspection_job(user, upload)

    registration = register_dataset_from_inspection(
        actor=user,
        inspection_job=inspection_job,
        title="Road network",
        slug="road-network",
        selected_layers=["roads"],
        start_import=False,
    )

    assert registration.created is True
    assert registration.import_job is None
    assert registration.dataset.kind == DatasetKind.VECTOR
    assert registration.dataset.status == DatasetStatus.REGISTERED
    assert registration.dataset.resource.resource_type == ResourceType.VECTOR_DATASET
    assert registration.version.source_upload == upload
    layer = VectorLayer.objects.get(version=registration.version)
    assert layer.source_layer_name == "roads"
    assert layer.source_crs == "EPSG:4326"
    assert layer.field_schema == [{"name": "name", "dtype": "object"}]

    repeated = register_dataset_from_inspection(
        actor=user,
        inspection_job=inspection_job,
        title="Ignored",
        slug="ignored",
        start_import=False,
    )
    assert repeated.created is False
    assert repeated.dataset == registration.dataset


@pytest.mark.django_db
def test_raster_registration_persists_inspection_and_marks_resource_ready() -> None:
    user = _user("raster-register")
    upload = _completed_upload(user, "surface.tif")
    inspection_job = _inspection_job(user, upload, raster=True)

    registration = register_dataset_from_inspection(
        actor=user,
        inspection_job=inspection_job,
        title="Surface",
        slug="surface",
    )

    registration.dataset.refresh_from_db()
    registration.dataset.resource.refresh_from_db()
    raster = RasterDataset.objects.get(dataset=registration.dataset)
    assert registration.dataset.kind == DatasetKind.RASTER
    assert registration.dataset.status == DatasetStatus.READY
    assert registration.dataset.resource.lifecycle_status == "READY"
    assert raster.width == 16
    assert raster.band_count == 1
    assert raster.cog_readiness == {"needs_conversion": True}


@pytest.mark.django_db
def test_registration_rejects_nonspatial_layer_selection() -> None:
    user = _user("bad-layer-register")
    upload = _completed_upload(user, "roads.geojson")
    inspection_job = _inspection_job(user, upload)

    with pytest.raises(ValueError, match="Unknown or nonspatial"):
        register_dataset_from_inspection(
            actor=user,
            inspection_job=inspection_job,
            title="Bad layer",
            slug="bad-layer",
            selected_layers=["metadata"],
            start_import=False,
        )


@pytest.mark.django_db
def test_dataset_registration_api_returns_registered_dataset(client) -> None:
    user = _user("dataset-api")
    upload = _completed_upload(user, "roads.geojson")
    inspection_job = _inspection_job(user, upload)
    client.force_login(user)

    response = client.post(
        "/api/v1/datasets/register",
        data=json.dumps(
            {
                "inspection_job_id": str(inspection_job.id),
                "title": "Roads",
                "slug": "roads",
                "selected_layers": ["roads"],
                "start_import": False,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "VECTOR"
    assert body["status"] == "REGISTERED"
    assert body["vector_layers"][0]["name"] == "roads"
