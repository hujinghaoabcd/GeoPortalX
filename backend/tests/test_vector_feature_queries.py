from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.gis.geos import Polygon
from django.db import connection
from django.test import override_settings
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


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _ready_layer(user: User) -> VectorLayer:
    token = uuid4().hex
    resource = Resource.objects.create(
        owner=user,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Queryable places",
        slug=f"queryable-places-{token}",
        description="Permission-aware feature queries",
        visibility=Visibility.PRIVATE,
        lifecycle_status=LifecycleStatus.READY,
    )
    upload = UploadSession.objects.create(
        created_by=user,
        resource=resource,
        original_filename="places.geojson",
        content_type="application/geo+json",
        declared_size=100,
        checksum_sha256="",
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{token}/places.geojson",
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
    extent = Polygon.from_bbox((118.0, 31.0, 120.0, 34.0))
    extent.srid = 4326
    return VectorLayer.objects.create(
        vector_dataset=vector,
        version=version,
        ordinal=1,
        source_layer_name="places",
        title="Places",
        status=VectorLayerStatus.READY,
        field_schema=[
            {
                "name": "gx_fid",
                "data_type": "bigint",
                "database_type": "int8",
                "nullable": False,
            },
            {
                "name": "name",
                "data_type": "text",
                "database_type": "text",
                "nullable": True,
            },
            {
                "name": "category",
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
        geometry_type="POINT",
        geometry_column="geom",
        srid=4326,
        feature_count=3,
        db_schema="geoportalx_data",
        db_table=f"v_{token}",
        tile_source_id=f"v_{token}",
        min_zoom=0,
        max_zoom=14,
        extent=extent,
    )


def _qualified(layer: VectorLayer) -> str:
    return (
        f"{connection.ops.quote_name(layer.db_schema)}."
        f"{connection.ops.quote_name(layer.db_table)}"
    )


def _create_query_table(layer: VectorLayer) -> None:
    table = _qualified(layer)
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE SCHEMA IF NOT EXISTS {connection.ops.quote_name(layer.db_schema)}"
        )
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute(
            f"""
            CREATE TABLE {table} (
                gx_fid bigint PRIMARY KEY,
                name text,
                category text,
                speed integer,
                geom geometry(Point, 4326) NOT NULL
            )
            """
        )
        cursor.executemany(
            f"""
            INSERT INTO {table} (gx_fid, name, category, speed, geom)
            VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            """,
            [
                (0, "Origin", "station", 30, 118.78, 32.04),
                (1, "Nearby", "road", 50, 118.80, 32.05),
                (2, "Outside", "road", 80, 119.50, 33.00),
            ],
        )
        cursor.execute(
            f"CREATE INDEX {connection.ops.quote_name(f'{layer.db_table}_geom_gix')} "
            f"ON {table} USING GIST (geom)"
        )


def _drop_query_table(layer: VectorLayer) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {_qualified(layer)}")


@pytest.fixture
def query_layer(db):
    owner = _user("feature-query-owner")
    layer = _ready_layer(owner)
    _create_query_table(layer)
    yield owner, layer
    _drop_query_table(layer)


def test_private_feature_queries_are_hidden_and_owner_can_page(client, query_layer) -> None:
    owner, layer = query_layer
    stranger = _user("feature-query-stranger")
    url = f"/api/v1/vector-layers/{layer.id}/features"

    assert client.get(url).status_code == 404
    client.force_login(stranger)
    assert client.get(url).status_code == 404

    client.force_login(owner)
    first = client.get(
        url,
        {
            "limit": 1,
            "bbox": "118.7,31.9,118.9,32.1",
            "fields": "name,speed",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert [feature["id"] for feature in first_body["features"]] == [0]
    assert first_body["features"][0]["properties"] == {"name": "Origin", "speed": 30}
    assert first_body["next_cursor"] == 0
    assert first["Cache-Control"] == "private, no-store"

    second = client.get(
        url,
        {
            "limit": 1,
            "cursor": first_body["next_cursor"],
            "bbox": "118.7,31.9,118.9,32.1",
            "fields": "name,speed",
        },
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [feature["id"] for feature in second_body["features"]] == [1]
    assert second_body["next_cursor"] is None


def test_feature_detail_can_omit_geometry(client, query_layer) -> None:
    owner, layer = query_layer
    client.force_login(owner)

    response = client.get(
        f"/api/v1/vector-layers/{layer.id}/features/1",
        {"fields": "name,category", "include_geometry": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["geometry"] is None
    assert body["properties"] == {"name": "Nearby", "category": "road"}
    assert body["geoportalx"]["layer_id"] == str(layer.id)
    assert client.get(
        f"/api/v1/vector-layers/{layer.id}/features/999"
    ).status_code == 404


def test_identify_returns_nearest_features_with_distance(client, query_layer) -> None:
    owner, layer = query_layer
    client.force_login(owner)

    response = client.get(
        f"/api/v1/vector-layers/{layer.id}/identify",
        {
            "longitude": 118.78,
            "latitude": 32.04,
            "tolerance_m": 3000,
            "limit": 2,
            "fields": "name",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [feature["id"] for feature in body["features"]] == [0, 1]
    assert body["features"][0]["distance_m"] == pytest.approx(0.0, abs=0.001)
    assert body["features"][1]["distance_m"] > 0
    assert body["query_point"] == [118.78, 32.04]


def test_invalid_feature_query_parameters_are_rejected(client, query_layer) -> None:
    owner, layer = query_layer
    client.force_login(owner)
    url = f"/api/v1/vector-layers/{layer.id}/features"

    assert client.get(url, {"bbox": "118,32,117,33"}).status_code == 400
    assert client.get(url, {"fields": "missing"}).status_code == 400
    assert client.get(url, {"limit": 0}).status_code == 400
    identify_url = f"/api/v1/vector-layers/{layer.id}/identify"
    assert client.get(
        identify_url,
        {"longitude": 181, "latitude": 32},
    ).status_code == 400


@override_settings(VECTOR_FEATURE_MAX_RESPONSE_BYTES=32)
def test_feature_response_size_is_bounded(client, query_layer) -> None:
    owner, layer = query_layer
    client.force_login(owner)

    response = client.get(f"/api/v1/vector-layers/{layer.id}/features")

    assert response.status_code == 413
