import json
import shutil
from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.utils import timezone

from modules.accounts.models import User
from modules.dataset_inspection.materialization import MaterializedUpload
from modules.datasets.handlers import run_vector_import
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
from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job, JobStatus
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.uploads.models import UploadSession, UploadStatus


@pytest.mark.django_db(transaction=True)
def test_vector_import_promotes_validated_layer_into_postgis(tmp_path, monkeypatch) -> None:
    if shutil.which("ogr2ogr") is None:
        pytest.skip("ogr2ogr is not installed")

    source = tmp_path / "roads.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "roads",
                "crs": {
                    "type": "name",
                    "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
                },
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "Main Street", "lanes": 2},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[0, 0], [1, 1]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    user = User.objects.create_user(
        username="vector-importer",
        email="vector-importer@example.com",
        password="test-password",
    )
    resource = Resource.objects.create(
        owner=user,
        resource_type=ResourceType.VECTOR_DATASET,
        title="Roads",
        slug="roads-import",
        visibility=Visibility.PRIVATE,
        lifecycle_status=LifecycleStatus.PROCESSING,
    )
    upload = UploadSession.objects.create(
        created_by=user,
        resource=resource,
        original_filename="roads.geojson",
        content_type="application/geo+json",
        declared_size=source.stat().st_size,
        checksum_sha256="",
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/roads.geojson",
        multipart_upload_id="upload-id",
        status=UploadStatus.COMPLETED,
        part_size=source.stat().st_size,
        part_count=1,
        actual_size=source.stat().st_size,
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
        status=DatasetStatus.IMPORTING,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_upload=upload,
        inspection_job=inspection_job,
        status=DatasetVersionStatus.IMPORTING,
        source_format="GeoJSON",
        source_checksum_sha256="",
        inspection_result={},
        created_by=user,
    )
    dataset.current_version = version
    dataset.save(update_fields=("current_version", "updated_at"))
    vector = VectorDataset.objects.create(dataset=dataset, layer_count=1)
    layer = VectorLayer.objects.create(
        vector_dataset=vector,
        version=version,
        ordinal=1,
        source_layer_name="roads",
        title="Roads",
        source_driver="GeoJSON",
        source_crs="EPSG:4326",
        source_bounds=[0, 0, 1, 1],
        field_schema=[],
        geometry_type="LineString",
    )
    import_job = Job.objects.create(
        created_by=user,
        resource=resource,
        job_type="vector-import",
        status=JobStatus.RUNNING,
        progress=1,
        input_parameters={"dataset_version_id": str(version.id)},
    )

    @contextmanager
    def fake_materialization(_upload):
        yield MaterializedUpload(
            upload_id=str(upload.id),
            path=source,
            size=source.stat().st_size,
            checksum_sha256="",
            content_type="application/geo+json",
        )

    monkeypatch.setattr(
        "modules.datasets.handlers.materialize_completed_upload",
        fake_materialization,
    )

    result = run_vector_import(
        JobExecutionContext(job_id=import_job.id),
        {"dataset_version_id": str(version.id)},
    )

    dataset.refresh_from_db()
    version.refresh_from_db()
    layer.refresh_from_db()
    resource.refresh_from_db()
    assert result["dataset_id"] == str(dataset.id)
    assert dataset.status == DatasetStatus.READY
    assert version.status == DatasetVersionStatus.READY
    assert layer.status == VectorLayerStatus.READY
    assert layer.db_schema
    assert layer.db_table
    assert layer.geometry_column == "geom"
    assert layer.srid == 4326
    assert layer.feature_count == 1
    assert any(field["name"] == "name" for field in layer.field_schema)
    assert resource.lifecycle_status == LifecycleStatus.READY
    assert resource.spatial_extent is not None
