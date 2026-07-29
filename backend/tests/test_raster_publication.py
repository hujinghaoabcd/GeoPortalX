import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.models import (
    DatasetKind,
    RasterPublication,
    RasterPublicationStatus,
    RasterRenderMode,
    RasterRenderSettings,
)
from modules.datasets.raster_services import request_raster_publication
from modules.datasets.services import register_dataset_from_inspection
from modules.jobs.models import Job, JobStatus
from modules.raster_tiles.rendering import (
    RasterRenderValidationError,
    update_raster_render_settings,
    validate_render_settings,
)
from modules.raster_tiles.selectors import raster_publication_accessible_to
from modules.uploads.models import UploadSession, UploadStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _raster_registration(user: User, *, slug: str):
    upload = UploadSession.objects.create(
        created_by=user,
        original_filename=f"{slug}.tif",
        content_type="image/tiff",
        declared_size=1024,
        checksum_sha256="a" * 64,
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{slug}.tif",
        multipart_upload_id="upload-id",
        status=UploadStatus.COMPLETED,
        part_size=1024,
        part_count=1,
        actual_size=1024,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )
    inspection = {
        "dataset_type": "raster",
        "format": "GeoTIFF",
        "driver": "GTiff",
        "width": 512,
        "height": 256,
        "band_count": 3,
        "crs": "EPSG:4326",
        "epsg": 4326,
        "bounds": [118.0, 31.0, 119.0, 32.0],
        "transform": [0.01, 0, 118, 0, -0.01, 32],
        "bands": [
            {"index": 1, "dtype": "uint16"},
            {"index": 2, "dtype": "uint16"},
            {"index": 3, "dtype": "uint16"},
        ],
        "image_structure": {},
        "cog_readiness": {"needs_conversion": True},
    }
    inspection_job = Job.objects.create(
        created_by=user,
        job_type="raster-inspect",
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
    registration = register_dataset_from_inspection(
        actor=user,
        inspection_job=inspection_job,
        title=slug.title(),
        slug=slug,
    )
    assert registration.dataset.kind == DatasetKind.RASTER
    return registration


def _ready_publication(user: User, *, slug: str = "raster-ready") -> RasterPublication:
    registration = _raster_registration(user, slug=slug)
    publication = RasterPublication.objects.create(
        raster_dataset=registration.dataset.raster,
        version=registration.version,
        created_by=user,
        status=RasterPublicationStatus.READY,
        bucket="geoportalx",
        object_key=f"rasters/{registration.dataset.id}/asset.cog.tif",
        checksum_sha256="b" * 64,
        content_type="image/tiff",
        object_size=4096,
        width=512,
        height=256,
        band_count=3,
        crs="EPSG:4326",
        epsg=4326,
        bounds=[118.0, 31.0, 119.0, 32.0],
        bands=[
            {"index": 1, "dtype": "uint16"},
            {"index": 2, "dtype": "uint16"},
            {"index": 3, "dtype": "uint16"},
        ],
        statistics=[
            {"band": 1, "percentile_2": 10.0, "percentile_98": 100.0},
            {"band": 2, "percentile_2": 20.0, "percentile_98": 200.0},
            {"band": 3, "percentile_2": 30.0, "percentile_98": 300.0},
        ],
        cog_profile={"validated": True, "layout": "COG"},
        min_zoom=5,
        max_zoom=14,
        completed_at=timezone.now(),
    )
    RasterRenderSettings.objects.create(
        publication=publication,
        mode=RasterRenderMode.RGB,
        bands=[1, 2, 3],
        rescale=[[10, 100], [20, 200], [30, 300]],
        resampling="bilinear",
        opacity=0.8,
        updated_by=user,
    )
    return publication


@pytest.mark.django_db
def test_raster_publication_request_is_idempotent(monkeypatch) -> None:
    user = _user("raster-request")
    registration = _raster_registration(user, slug="raster-request")
    queued_job = Job.objects.create(
        created_by=user,
        resource=registration.dataset.resource,
        job_type="raster-publish",
        queue="raster",
        status=JobStatus.PENDING,
    )
    create_job = Mock(return_value=queued_job)
    monkeypatch.setattr(
        "modules.datasets.raster_services.create_and_dispatch_job",
        create_job,
    )

    first = request_raster_publication(actor=user, dataset_id=registration.dataset.id)
    second = request_raster_publication(actor=user, dataset_id=registration.dataset.id)

    assert first.created is True
    assert first.publication.status == RasterPublicationStatus.PENDING
    assert first.job == queued_job
    assert second.publication == first.publication
    assert second.job == queued_job
    assert create_job.call_count == 1


@pytest.mark.django_db
def test_raster_render_validation_and_update() -> None:
    owner = _user("raster-style-owner")
    publication = _ready_publication(owner, slug="raster-style")

    normalized = validate_render_settings(
        publication=publication,
        mode=RasterRenderMode.SINGLE_BAND,
        bands=[2],
        rescale=[[5, 250]],
        colormap_name="terrain",
        resampling="cubic",
        opacity=0.55,
    )
    assert normalized["bands"] == [2]
    assert normalized["colormap_name"] == "terrain"

    updated = update_raster_render_settings(
        actor=owner,
        publication=publication,
        **normalized,
    )
    assert updated.mode == RasterRenderMode.SINGLE_BAND
    assert updated.revision == 2
    assert updated.opacity == 0.55

    with pytest.raises(RasterRenderValidationError, match="requires 3 unique bands"):
        validate_render_settings(
            publication=publication,
            mode=RasterRenderMode.RGB,
            bands=[1, 1, 3],
            rescale=[[0, 1], [0, 1], [0, 1]],
            colormap_name=None,
            resampling="bilinear",
            opacity=1.0,
        )


@pytest.mark.django_db
def test_raster_selector_respects_visibility_and_active_version() -> None:
    owner = _user("raster-selector-owner")
    stranger = _user("raster-selector-stranger")
    publication = _ready_publication(owner, slug="raster-selector")

    assert raster_publication_accessible_to(owner, publication.raster_dataset_id) == publication
    assert raster_publication_accessible_to(stranger, publication.raster_dataset_id) is None
    assert raster_publication_accessible_to(AnonymousUser(), publication.raster_dataset_id) is None

    resource = publication.raster_dataset.dataset.resource
    resource.visibility = "PUBLIC"
    resource.save(update_fields=("visibility", "updated_at"))
    assert (
        raster_publication_accessible_to(AnonymousUser(), publication.raster_dataset_id)
        == publication
    )

    publication.raster_dataset.dataset.current_version = None
    publication.raster_dataset.dataset.save(update_fields=("current_version", "updated_at"))
    assert raster_publication_accessible_to(owner, publication.raster_dataset_id) is None


@pytest.mark.django_db
def test_raster_source_and_rendering_api(client) -> None:
    owner = _user("raster-api-owner")
    publication = _ready_publication(owner, slug="raster-api")
    client.force_login(owner)

    source_response = client.get(
        f"/api/v1/raster-datasets/{publication.raster_dataset_id}/source"
    )
    assert source_response.status_code == 200
    source = source_response.json()
    assert source["source"]["type"] == "raster"
    assert "{z}/{x}/{y}.png" in source["source"]["tiles"][0]
    assert source["opacity"] == 0.8

    rendering_response = client.put(
        f"/api/v1/raster-datasets/{publication.raster_dataset_id}/rendering",
        data=json.dumps(
            {
                "mode": "SINGLE_BAND",
                "bands": [1],
                "rescale": [[0, 120]],
                "colormap_name": "viridis",
                "resampling": "nearest",
                "opacity": 0.6,
            }
        ),
        content_type="application/json",
    )
    assert rendering_response.status_code == 200
    body = rendering_response.json()
    assert body["mode"] == "SINGLE_BAND"
    assert body["revision"] == 2

    stranger = _user("raster-api-stranger")
    client.force_login(stranger)
    response = client.get(
        f"/api/v1/raster-datasets/{publication.raster_dataset_id}/source"
    )
    assert response.status_code == 404
