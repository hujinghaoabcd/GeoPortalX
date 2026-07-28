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
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.uploads.models import UploadSession, UploadStatus


class FakeMartinResponse:
    status = 200

    def __init__(self, body: bytes = b"vector-tile") -> None:
        self.body = body
        self.headers = {
            "Content-Type": "application/vnd.mapbox-vector-tile",
            "ETag": '"tile-etag"',
            "Last-Modified": "Tue, 28 Jul 2026 10:00:00 GMT",
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _ready_layer(
    user: User,
    *,
    visibility: str = Visibility.PRIVATE,
) -> VectorLayer:
    token = uuid4().hex
    resource = Resource.objects.create(
        owner=user,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Published roads",
        slug=f"published-roads-{token}",
        description="Permission-aware vector tiles",
        visibility=visibility,
        lifecycle_status=LifecycleStatus.READY,
    )
    upload = UploadSession.objects.create(
        created_by=user,
        resource=resource,
        original_filename="roads.geojson",
        content_type="application/geo+json",
        declared_size=100,
        checksum_sha256="",
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{token}/roads.geojson",
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
        created_by=user,
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
        created_by=user,
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
        ],
        field_statistics=[{"name": "name", "distinct_count": 2}],
        quality_report={
            "sample_size": 2,
            "invalid_geometry_count": 0,
        },
        geometry_type="MULTILINESTRING",
        geometry_column="geom",
        srid=4326,
        feature_count=2,
        db_schema="geoportalx_data",
        db_table=f"v_{token}",
        tile_source_id=f"v_{token}",
        min_zoom=0,
        max_zoom=14,
        extent=extent,
    )


@pytest.mark.django_db
def test_public_layer_exposes_permission_aware_tilejson_anonymously(client) -> None:
    owner = _user("public-layer-owner")
    layer = _ready_layer(owner, visibility=Visibility.PUBLIC)

    response = client.get(f"/api/v1/vector-layers/{layer.id}/tilejson")

    assert response.status_code == 200
    body = response.json()
    assert body["tilejson"] == "3.0.0"
    assert body["vector_layers"][0]["id"] == layer.tile_source_id
    assert body["tiles"][0].endswith(
        f"/api/v1/vector-layers/{layer.id}/tiles/{{z}}/{{x}}/{{y}}"
        f"?version={layer.version_id}"
    )
    assert response["Cache-Control"].startswith("public")


@pytest.mark.django_db
def test_private_layer_is_hidden_and_owner_can_read_quality(client) -> None:
    owner = _user("private-layer-owner")
    stranger = _user("private-layer-stranger")
    layer = _ready_layer(owner)

    assert client.get(f"/api/v1/vector-layers/{layer.id}/tilejson").status_code == 404
    client.force_login(stranger)
    assert client.get(f"/api/v1/vector-layers/{layer.id}/source").status_code == 404

    client.force_login(owner)
    response = client.get(f"/api/v1/vector-layers/{layer.id}/quality")
    assert response.status_code == 200
    assert response.json()["quality_report"]["invalid_geometry_count"] == 0
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.django_db
def test_tile_proxy_checks_permission_and_forwards_martin_tile(client, monkeypatch) -> None:
    owner = _user("tile-proxy-owner")
    layer = _ready_layer(owner)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["accept"] = request.get_header("Accept")
        return FakeMartinResponse()

    monkeypatch.setattr("modules.vector_tiles.services.urlopen", fake_urlopen)
    client.force_login(owner)
    response = client.get(
        f"/api/v1/vector-layers/{layer.id}/tiles/0/0/0",
        HTTP_ACCEPT_ENCODING="gzip",
    )

    assert response.status_code == 200
    assert response.content == b"vector-tile"
    assert response["ETag"] == '"tile-etag"'
    assert response["Cache-Control"] == "private, no-store"
    assert captured["url"].endswith(f"/{layer.tile_source_id}/0/0/0")
    assert captured["accept"] == "application/x-protobuf"


@pytest.mark.django_db
def test_invalid_tile_coordinates_are_rejected_before_upstream(client, monkeypatch) -> None:
    owner = _user("invalid-tile-owner")
    layer = _ready_layer(owner)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("Martin should not be called")

    monkeypatch.setattr("modules.vector_tiles.services.urlopen", fail_urlopen)
    client.force_login(owner)
    response = client.get(f"/api/v1/vector-layers/{layer.id}/tiles/2/4/0")

    assert response.status_code == 404


@pytest.mark.django_db
def test_maplibre_source_descriptor_uses_protected_tiles(client) -> None:
    owner = _user("source-descriptor-owner")
    layer = _ready_layer(owner)
    client.force_login(owner)

    response = client.get(f"/api/v1/vector-layers/{layer.id}/source")

    assert response.status_code == 200
    body = response.json()
    assert body["source"]["type"] == "vector"
    assert body["source_layer"] == layer.tile_source_id
    assert body["geometry_type"] == "MULTILINESTRING"
    assert body["bounds"] == [118.0, 31.0, 119.0, 33.0]
