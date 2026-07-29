import json
from datetime import timedelta
from uuid import uuid4

import pytest
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
from modules.maps.document_schema import validate_map_document
from modules.maps.models import (
    MapDocumentVersionActivation,
    MapLayerBindingMode,
    MapLayerReference,
    MapVersionActivationAction,
)
from modules.maps.services import (
    activate_map_document_version,
    create_map_document,
    create_map_document_version,
)
from modules.permissions.models import (
    PermissionAction,
    PermissionSubjectType,
    ResourcePermission,
)
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.resources.services import create_resource
from modules.uploads.models import UploadSession, UploadStatus


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _ready_vector_dataset(owner: User, slug: str) -> tuple[Dataset, DatasetVersion]:
    resource = create_resource(
        owner=owner,
        resource_type=ResourceType.VECTOR_DATASET,
        title=slug.replace("-", " ").title(),
        slug=slug,
    )
    resource.lifecycle_status = LifecycleStatus.READY
    resource.save(update_fields=("lifecycle_status", "updated_at"))
    upload = UploadSession.objects.create(
        created_by=owner,
        original_filename=f"{slug}.geojson",
        content_type="application/geo+json",
        declared_size=1024,
        checksum_sha256="a" * 64,
        bucket="geoportalx",
        object_key=f"uploads/{owner.id}/{uuid4()}.geojson",
        multipart_upload_id=str(uuid4()),
        status=UploadStatus.COMPLETED,
        part_size=1024,
        part_count=1,
        actual_size=1024,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )
    inspection_job = Job.objects.create(
        created_by=owner,
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
        source_checksum_sha256="a" * 64,
        inspection_result={},
        created_by=owner,
        imported_at=timezone.now(),
        activated_at=timezone.now(),
        activation_count=1,
    )
    dataset.current_version = version
    dataset.save(update_fields=("current_version", "updated_at"))
    vector_dataset = VectorDataset.objects.create(
        dataset=dataset,
        layer_count=1,
        imported_layer_count=1,
    )
    VectorLayer.objects.create(
        vector_dataset=vector_dataset,
        version=version,
        ordinal=0,
        source_layer_name="roads",
        title="Roads",
        status=VectorLayerStatus.READY,
        source_driver="GeoJSON",
        source_crs="EPSG:4326",
        field_schema=[{"name": "name", "dtype": "string"}],
        geometry_type="LineString",
        srid=4326,
        feature_count=2,
    )
    return dataset, version


def _document(
    dataset: Dataset,
    *,
    layer_id: str = "roads",
    binding: str = "CURRENT",
    version: DatasetVersion | None = None,
    zoom: float = 8.0,
) -> dict:
    return {
        "schema_version": 1,
        "view": {
            "center": [118.8, 32.0],
            "zoom": zoom,
            "bearing": 0,
            "pitch": 0,
        },
        "layers": [
            {
                "id": layer_id,
                "title": "Roads",
                "kind": "VECTOR",
                "dataset_id": str(dataset.id),
                "binding": binding,
                "dataset_version_id": str(version.id) if version else None,
                "source_layer_name": "roads",
                "visible": True,
                "opacity": 0.8,
                "style": {"line-color": "#3366ff"},
                "popup": {"fields": ["name"]},
                "legend": {"title": "Roads"},
            }
        ],
        "metadata": {"locale": "zh-CN"},
    }


@pytest.mark.django_db
def test_create_map_document_persists_version_and_normalized_reference() -> None:
    owner = _user("map-owner")
    dataset, dataset_version = _ready_vector_dataset(owner, "map-roads")

    result = create_map_document(
        actor=owner,
        title="Nanjing roads",
        slug="nanjing-roads-map",
        document=_document(
            dataset,
            binding=MapLayerBindingMode.PINNED,
            version=dataset_version,
        ),
        note="Initial map",
    )

    result.map_document.refresh_from_db()
    result.map_document.resource.refresh_from_db()
    assert result.map_document.resource.resource_type == ResourceType.MAP
    assert result.map_document.resource.lifecycle_status == LifecycleStatus.READY
    assert result.map_document.current_version_id == result.version.id
    assert result.version.version_number == 1
    assert result.version.schema_version == 1
    assert len(result.version.checksum_sha256) == 64
    assert result.version.document["view"]["center"] == [118.8, 32.0]

    reference = MapLayerReference.objects.get(version=result.version)
    assert reference.ordinal == 0
    assert reference.client_layer_id == "roads"
    assert reference.binding_mode == MapLayerBindingMode.PINNED
    assert reference.dataset_version == dataset_version
    assert reference.source_layer_name == "roads"
    assert reference.opacity == 0.8

    activation = MapDocumentVersionActivation.objects.get(map_document=result.map_document)
    assert activation.action == MapVersionActivationAction.INITIAL
    assert activation.to_version == result.version


@pytest.mark.django_db
def test_map_document_versions_are_immutable_and_support_rollback() -> None:
    owner = _user("map-version-owner")
    dataset, _version = _ready_vector_dataset(owner, "version-roads")
    initial = create_map_document(
        actor=owner,
        title="Versioned map",
        slug="versioned-map",
        document=_document(dataset, zoom=7),
    )
    second = create_map_document_version(
        actor=owner,
        map_document_id=initial.map_document.id,
        document=_document(dataset, zoom=12),
        note="Zoomed view",
        activate=True,
    )

    initial.version.refresh_from_db()
    second.map_document.refresh_from_db()
    assert initial.version.document["view"]["zoom"] == 7
    assert second.version.document["view"]["zoom"] == 12
    assert second.map_document.current_version_id == second.version.id
    assert second.version.version_number == 2

    activate_map_document_version(
        actor=owner,
        map_document_id=initial.map_document.id,
        version_id=initial.version.id,
        note="Rollback after review",
    )
    initial.map_document.refresh_from_db()
    initial.version.refresh_from_db()
    second.version.refresh_from_db()
    assert initial.map_document.current_version_id == initial.version.id
    assert initial.version.activation_count == 2
    assert second.version.deactivated_at is not None
    latest_activation = MapDocumentVersionActivation.objects.first()
    assert latest_activation is not None
    assert latest_activation.action == MapVersionActivationAction.ROLLBACK


@pytest.mark.django_db
def test_source_permission_is_checked_on_save_and_rechecked_on_activation() -> None:
    source_owner = _user("source-owner")
    map_owner = _user("shared-map-owner")
    dataset, _version = _ready_vector_dataset(source_owner, "private-roads")

    with pytest.raises(PermissionError, match="source is not accessible"):
        create_map_document(
            actor=map_owner,
            title="Denied map",
            slug="denied-map",
            document=_document(dataset),
        )

    grant = ResourcePermission.objects.create(
        resource=dataset.resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=map_owner.id,
        action=PermissionAction.VIEW,
        granted_by=source_owner,
    )
    result = create_map_document(
        actor=map_owner,
        title="Shared source map",
        slug="shared-source-map",
        document=_document(dataset),
    )
    grant.delete()

    with pytest.raises(PermissionError, match="source is not accessible"):
        activate_map_document_version(
            actor=map_owner,
            map_document_id=result.map_document.id,
            version_id=result.version.id,
        )


@pytest.mark.django_db
def test_document_schema_rejects_duplicate_ids_invalid_binding_and_nan() -> None:
    owner = _user("schema-owner")
    dataset, dataset_version = _ready_vector_dataset(owner, "schema-roads")
    duplicate = _document(dataset)
    duplicate["layers"].append(dict(duplicate["layers"][0]))
    with pytest.raises(ValueError, match="layer ids must be unique"):
        validate_map_document(duplicate)

    invalid_binding = _document(dataset)
    invalid_binding["layers"][0]["dataset_version_id"] = str(dataset_version.id)
    with pytest.raises(ValueError, match="CURRENT layer bindings"):
        validate_map_document(invalid_binding)

    non_finite = _document(dataset)
    non_finite["layers"][0]["style"] = {"line-width": float("nan")}
    with pytest.raises(ValueError, match="must be finite"):
        validate_map_document(non_finite)


@pytest.mark.django_db
def test_map_api_versions_and_protects_public_document_source_details(client) -> None:
    owner = _user("map-api-owner")
    outsider = _user("map-api-outsider")
    dataset, _version = _ready_vector_dataset(owner, "api-roads")
    client.force_login(owner)

    response = client.post(
        "/api/v1/maps/",
        data=json.dumps(
            {
                "title": "API map",
                "slug": "api-map",
                "visibility": Visibility.PRIVATE,
                "document": _document(dataset),
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["current_version_number"] == 1
    assert body["current_document"]["layers"][0]["id"] == "roads"
    assert body["layer_references"][0]["dataset_version_id"] is None
    map_document_id = body["id"]

    version_response = client.post(
        f"/api/v1/maps/{map_document_id}/versions",
        data=json.dumps(
            {
                "document": _document(dataset, zoom=11),
                "note": "Second version",
                "activate": True,
            }
        ),
        content_type="application/json",
    )
    assert version_response.status_code == 201
    assert version_response.json()["current_version_number"] == 2

    Resource.objects.filter(pk=body["resource_id"]).update(visibility=Visibility.PUBLIC)
    client.force_login(outsider)
    listing = client.get("/api/v1/maps/")
    assert listing.status_code == 200
    assert any(item["id"] == map_document_id for item in listing.json())

    hidden = client.get(f"/api/v1/maps/{map_document_id}")
    assert hidden.status_code == 404

    ResourcePermission.objects.create(
        resource=dataset.resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=outsider.id,
        action=PermissionAction.VIEW,
        granted_by=owner,
    )
    revealed = client.get(f"/api/v1/maps/{map_document_id}")
    assert revealed.status_code == 200
    assert revealed.json()["current_document"]["layers"][0]["dataset_id"] == str(
        dataset.id
    )
