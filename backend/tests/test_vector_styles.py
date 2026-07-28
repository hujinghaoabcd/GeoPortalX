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
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.uploads.models import UploadSession, UploadStatus
from modules.vector_styles.models import VectorStyle, VectorStyleMode


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _ready_layer(
    owner: User,
    *,
    geometry_type: str = "MULTILINESTRING",
    visibility: str = Visibility.PRIVATE,
) -> VectorLayer:
    token = uuid4().hex
    resource = Resource.objects.create(
        owner=owner,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Styled roads",
        slug=f"styled-roads-{token}",
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
            },
            {
                "name": "road_class",
                "data_type": "text",
                "database_type": "text",
            },
            {
                "name": "speed",
                "data_type": "integer",
                "database_type": "int4",
            },
        ],
        field_statistics=[
            {
                "name": "road_class",
                "data_type": "text",
                "distinct_count": 3,
                "top_values": [
                    {"value": "primary", "count": 20},
                    {"value": "secondary", "count": 12},
                    {"value": "local", "count": 8},
                ],
            },
            {
                "name": "speed",
                "data_type": "integer",
                "distinct_count": 40,
                "minimum": 0,
                "maximum": 100,
                "average": 48.5,
            },
        ],
        geometry_type=geometry_type,
        geometry_column="geom",
        srid=4326,
        feature_count=40,
        db_schema="geoportalx_data",
        db_table=f"v_{token}",
        tile_source_id=f"v_{token}",
        extent=extent,
    )


@pytest.mark.django_db
def test_ready_layer_receives_persistent_default_style(client) -> None:
    owner = _user("style-owner")
    layer = _ready_layer(owner)
    client.force_login(owner)

    assert VectorStyle.objects.filter(layer=layer).exists()
    response = client.get(f"/api/v1/vector-layers/{layer.id}/style")

    assert response.status_code == 200
    body = response.json()
    assert body["style"]["mode"] == VectorStyleMode.SIMPLE
    assert body["can_edit"] is True
    assert body["legend"] == [{"label": "全部要素", "color": "#2563eb"}]
    assert body["maplibre_layers"][0]["type"] == "line"


@pytest.mark.django_db
def test_owner_can_apply_categorical_style(client) -> None:
    owner = _user("categorical-owner")
    layer = _ready_layer(owner, geometry_type="MULTIPOLYGON")
    client.force_login(owner)

    response = client.put(
        f"/api/v1/vector-layers/{layer.id}/style",
        data=json.dumps(
            {
                "mode": "CATEGORICAL",
                "field_name": "road_class",
                "classification_method": "UNIQUE_VALUES",
                "class_count": 3,
                "palette": "CATEGORY10",
                "symbol": {"color": "#112233", "opacity": 0.55},
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["style"]["mode"] == "CATEGORICAL"
    assert [item["label"] for item in body["legend"][:3]] == [
        "primary",
        "secondary",
        "local",
    ]
    expression = body["maplibre_layers"][0]["paint"]["fill-color"]
    assert expression[0] == "match"
    assert expression[1][0] == "to-string"
    assert body["maplibre_layers"][1]["type"] == "line"


@pytest.mark.django_db
def test_owner_can_apply_equal_interval_style(client) -> None:
    owner = _user("graduated-owner")
    layer = _ready_layer(owner)
    client.force_login(owner)

    response = client.put(
        f"/api/v1/vector-layers/{layer.id}/style",
        data=json.dumps(
            {
                "mode": "GRADUATED",
                "field_name": "speed",
                "classification_method": "EQUAL_INTERVAL",
                "class_count": 4,
                "palette": "VIRIDIS",
                "symbol": {"color": "#2563eb", "opacity": 0.8, "width": 3},
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["style"]["classes"]) == 4
    assert body["style"]["classes"][0]["min"] == 0
    assert body["style"]["classes"][-1]["max"] == 100
    expression = body["maplibre_layers"][0]["paint"]["line-color"]
    assert expression[0] == "step"


@pytest.mark.django_db
def test_public_viewer_can_read_but_not_edit_style(client) -> None:
    owner = _user("public-style-owner")
    viewer = _user("public-style-viewer")
    layer = _ready_layer(owner, visibility=Visibility.PUBLIC)
    client.force_login(viewer)

    readable = client.get(f"/api/v1/vector-layers/{layer.id}/style")
    assert readable.status_code == 200
    assert readable.json()["can_edit"] is False

    denied = client.put(
        f"/api/v1/vector-layers/{layer.id}/style",
        data=json.dumps({"mode": "SIMPLE", "symbol": {"color": "#000000"}}),
        content_type="application/json",
    )
    assert denied.status_code == 404


@pytest.mark.django_db
def test_style_validation_rejects_unknown_fields_and_colors(client) -> None:
    owner = _user("invalid-style-owner")
    layer = _ready_layer(owner)
    client.force_login(owner)

    unknown_field = client.put(
        f"/api/v1/vector-layers/{layer.id}/style",
        data=json.dumps(
            {
                "mode": "CATEGORICAL",
                "field_name": "not_a_field",
                "class_count": 3,
                "palette": "BLUES",
            }
        ),
        content_type="application/json",
    )
    assert unknown_field.status_code == 400

    invalid_color = client.put(
        f"/api/v1/vector-layers/{layer.id}/style",
        data=json.dumps(
            {"mode": "SIMPLE", "symbol": {"color": "url(javascript:alert(1))"}}
        ),
        content_type="application/json",
    )
    assert invalid_color.status_code == 400
