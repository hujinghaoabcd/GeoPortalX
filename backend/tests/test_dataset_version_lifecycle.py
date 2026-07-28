import json
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Polygon
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.handlers import _mark_import_failed
from modules.datasets.models import (
    DatasetStatus,
    DatasetVersionActivation,
    DatasetVersionActivationAction,
    DatasetVersionStatus,
    VectorLayerStatus,
)
from modules.datasets.services import (
    activate_ready_dataset_version,
    register_dataset_from_inspection,
    register_dataset_replacement_from_inspection,
)
from modules.jobs.models import Job, JobStatus
from modules.resources.models import (
    LifecycleStatus,
    Resource,
    ResourceType,
    Visibility,
)
from modules.uploads.models import UploadSession, UploadStatus
from modules.vector_tiles.selectors import vector_layer_accessible_to


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _resource(user: User) -> Resource:
    return Resource.objects.create(
        owner=user,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Roads",
        slug=f"roads-{user.username}",
        visibility=Visibility.PRIVATE,
        lifecycle_status=LifecycleStatus.DRAFT,
    )


def _completed_upload(
    user: User,
    resource: Resource,
    filename: str,
) -> UploadSession:
    return UploadSession.objects.create(
        created_by=user,
        resource=resource,
        original_filename=filename,
        content_type="application/geo+json",
        declared_size=1024,
        checksum_sha256="a" * 64,
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/{filename}",
        multipart_upload_id=f"upload-{filename}",
        status=UploadStatus.COMPLETED,
        part_size=1024,
        part_count=1,
        actual_size=1024,
        object_etag="etag",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )


def _inspection_job(
    user: User,
    resource: Resource,
    upload: UploadSession,
    *,
    layer_name: str,
) -> Job:
    return Job.objects.create(
        created_by=user,
        resource=resource,
        job_type="vector-inspect",
        status=JobStatus.SUCCEEDED,
        progress=100,
        result_payload={
            "upload": {
                "id": str(upload.id),
                "sha256": "a" * 64,
                "resource_id": str(resource.id),
            },
            "inspection": {
                "dataset_type": "vector",
                "format": "GeoJSON",
                "layer_count": 1,
                "layers": [
                    {
                        "name": layer_name,
                        "driver": "GeoJSON",
                        "geometry_type": "LineString",
                        "feature_count": 2,
                        "crs": "EPSG:4326",
                        "bounds": [0, 0, 1, 1],
                        "fields": [
                            {
                                "name": "name",
                                "data_type": "text",
                                "database_type": "text",
                            }
                        ],
                    }
                ],
                "warnings": [],
            },
        },
    )


def _mark_version_ready(version, *, offset: float = 0.0):
    layer = version.vector_layers.get()
    extent = Polygon.from_bbox((offset, offset, offset + 1, offset + 1))
    extent.srid = 4326
    layer.status = VectorLayerStatus.READY
    layer.db_schema = "geoportalx_data"
    layer.db_table = f"v_{layer.id.hex}"
    layer.tile_source_id = layer.db_table
    layer.geometry_column = "geom"
    layer.geometry_type = "MULTILINESTRING"
    layer.srid = 4326
    layer.feature_count = 2
    layer.extent = extent
    layer.save()
    version.status = DatasetVersionStatus.READY
    version.imported_at = timezone.now()
    version.save(update_fields=("status", "imported_at"))
    return layer


def _active_dataset(user: User):
    resource = _resource(user)
    upload = _completed_upload(user, resource, "roads-v1.geojson")
    inspection = _inspection_job(
        user,
        resource,
        upload,
        layer_name="roads",
    )
    registration = register_dataset_from_inspection(
        actor=user,
        inspection_job=inspection,
        title="Roads",
        slug="ignored-existing-resource",
        selected_layers=["roads"],
        start_import=False,
    )
    layer = _mark_version_ready(registration.version)
    activate_ready_dataset_version(
        actor=user,
        dataset_id=registration.dataset.id,
        version_id=registration.version.id,
        requested_action=DatasetVersionActivationAction.INITIAL,
    )
    registration.dataset.refresh_from_db()
    resource.refresh_from_db()
    return registration.dataset, registration.version, layer, resource


@pytest.mark.django_db
def test_replacement_registration_preserves_active_version_and_publication() -> None:
    user = _user("version-candidate")
    dataset, active_version, active_layer, resource = _active_dataset(user)
    upload = _completed_upload(user, resource, "roads-v2.geojson")
    inspection = _inspection_job(
        user,
        resource,
        upload,
        layer_name="roads_v2",
    )

    replacement = register_dataset_replacement_from_inspection(
        actor=user,
        dataset_id=dataset.id,
        inspection_job=inspection,
        selected_layers=["roads_v2"],
        start_import=False,
    )

    dataset.refresh_from_db()
    resource.refresh_from_db()
    assert replacement.version.version_number == 2
    assert replacement.version.status == DatasetVersionStatus.REGISTERED
    assert dataset.current_version_id == active_version.id
    assert dataset.status == DatasetStatus.READY
    assert resource.lifecycle_status == LifecycleStatus.READY
    assert vector_layer_accessible_to(user, active_layer.id) is not None
    candidate_layer = replacement.version.vector_layers.get()
    assert vector_layer_accessible_to(user, candidate_layer.id) is None


@pytest.mark.django_db
def test_activation_and_rollback_switch_only_the_published_version() -> None:
    user = _user("version-rollback")
    dataset, version_one, layer_one, resource = _active_dataset(user)
    upload = _completed_upload(user, resource, "roads-v2.geojson")
    inspection = _inspection_job(
        user,
        resource,
        upload,
        layer_name="roads_v2",
    )
    replacement = register_dataset_replacement_from_inspection(
        actor=user,
        dataset_id=dataset.id,
        inspection_job=inspection,
        selected_layers=["roads_v2"],
        start_import=False,
    )
    layer_two = _mark_version_ready(replacement.version, offset=10)

    activated = activate_ready_dataset_version(
        actor=user,
        dataset_id=dataset.id,
        version_id=replacement.version.id,
        note="Publish replacement",
    )
    dataset.refresh_from_db()
    version_one.refresh_from_db()
    replacement.version.refresh_from_db()
    assert activated.changed is True
    assert activated.activation.action == DatasetVersionActivationAction.REPLACEMENT
    assert dataset.current_version_id == replacement.version.id
    assert version_one.deactivated_at is not None
    assert replacement.version.activation_count == 1
    assert vector_layer_accessible_to(user, layer_one.id) is None
    assert vector_layer_accessible_to(user, layer_two.id) is not None

    rolled_back = activate_ready_dataset_version(
        actor=user,
        dataset_id=dataset.id,
        version_id=version_one.id,
        requested_action=DatasetVersionActivationAction.ROLLBACK,
        note="Rollback after validation",
    )
    dataset.refresh_from_db()
    version_one.refresh_from_db()
    replacement.version.refresh_from_db()
    assert rolled_back.activation.action == DatasetVersionActivationAction.ROLLBACK
    assert dataset.current_version_id == version_one.id
    assert version_one.activation_count == 2
    assert version_one.deactivated_at is None
    assert replacement.version.deactivated_at is not None
    assert vector_layer_accessible_to(user, layer_one.id) is not None
    assert vector_layer_accessible_to(user, layer_two.id) is None
    assert DatasetVersionActivation.objects.filter(dataset=dataset).count() == 3


@pytest.mark.django_db
def test_failed_candidate_does_not_take_the_active_dataset_offline() -> None:
    user = _user("version-failure")
    dataset, active_version, active_layer, resource = _active_dataset(user)
    upload = _completed_upload(user, resource, "roads-v2.geojson")
    inspection = _inspection_job(
        user,
        resource,
        upload,
        layer_name="roads_v2",
    )
    replacement = register_dataset_replacement_from_inspection(
        actor=user,
        dataset_id=dataset.id,
        inspection_job=inspection,
        selected_layers=["roads_v2"],
        start_import=False,
    )
    candidate_layer = replacement.version.vector_layers.get()
    candidate_layer.status = VectorLayerStatus.IMPORTING
    candidate_layer.save(update_fields=("status", "updated_at"))
    replacement.version.status = DatasetVersionStatus.IMPORTING
    replacement.version.save(update_fields=("status",))

    _mark_import_failed(
        replacement.version,
        [candidate_layer],
        RuntimeError("candidate failed"),
    )

    dataset.refresh_from_db()
    resource.refresh_from_db()
    replacement.version.refresh_from_db()
    active_version.refresh_from_db()
    assert dataset.current_version_id == active_version.id
    assert dataset.status == DatasetStatus.READY
    assert resource.lifecycle_status == LifecycleStatus.READY
    assert replacement.version.status == DatasetVersionStatus.FAILED
    assert vector_layer_accessible_to(user, active_layer.id) is not None


@pytest.mark.django_db
def test_dataset_version_api_registers_and_activates_replacement(client) -> None:
    user = _user("version-api")
    dataset, version_one, _layer_one, resource = _active_dataset(user)
    upload = _completed_upload(user, resource, "roads-v2.geojson")
    inspection = _inspection_job(
        user,
        resource,
        upload,
        layer_name="roads_v2",
    )
    client.force_login(user)

    response = client.post(
        f"/api/v1/datasets/{dataset.id}/versions",
        data=json.dumps(
            {
                "inspection_job_id": str(inspection.id),
                "selected_layers": ["roads_v2"],
                "start_import": False,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201
    body = response.json()
    candidate_id = next(
        version["id"]
        for version in body["versions"]
        if version["version_number"] == 2
    )
    assert body["current_version_id"] == str(version_one.id)

    candidate = dataset.versions.get(pk=candidate_id)
    _mark_version_ready(candidate, offset=10)
    activation = client.post(
        f"/api/v1/datasets/{dataset.id}/versions/{candidate.id}/activate",
        data=json.dumps({"note": "Publish through API"}),
        content_type="application/json",
    )
    assert activation.status_code == 200
    activated_body = activation.json()
    assert activated_body["current_version_id"] == str(candidate.id)
    assert activated_body["version_activations"][0]["to_version_id"] == str(candidate.id)
