from datetime import timedelta
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
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
from modules.datasets.reconciliation import reconcile_vector_imports
from modules.jobs.models import Job, JobStatus
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.uploads.models import UploadSession, UploadStatus


def _user(name: str) -> User:
    return User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="test-password",
    )


def _dataset(user: User, slug: str) -> tuple[Dataset, VectorDataset]:
    resource = Resource.objects.create(
        owner=user,
        resource_type=ResourceType.VECTOR_DATASET,
        title=slug,
        slug=slug,
        visibility=Visibility.PRIVATE,
        lifecycle_status=LifecycleStatus.READY,
    )
    dataset = Dataset.objects.create(
        resource=resource,
        kind=DatasetKind.VECTOR,
        status=DatasetStatus.READY,
    )
    return dataset, VectorDataset.objects.create(dataset=dataset)


def _version(
    *,
    dataset: Dataset,
    user: User,
    number: int,
    status: str,
) -> DatasetVersion:
    upload = UploadSession.objects.create(
        created_by=user,
        resource=dataset.resource,
        original_filename=f"roads-{number}.geojson",
        content_type="application/geo+json",
        declared_size=128,
        checksum_sha256=str(number) * 64,
        bucket="geoportalx",
        object_key=f"uploads/{user.id}/roads-{number}.geojson",
        multipart_upload_id=f"upload-{number}-{uuid4()}",
        status=UploadStatus.COMPLETED,
        part_size=128,
        part_count=1,
        actual_size=128,
        object_etag=f"etag-{number}",
        expires_at=timezone.now() + timedelta(hours=1),
        completed_at=timezone.now(),
    )
    inspection_job = Job.objects.create(
        created_by=user,
        resource=dataset.resource,
        job_type="vector-inspect",
        status=JobStatus.SUCCEEDED,
        progress=100,
    )
    return DatasetVersion.objects.create(
        dataset=dataset,
        version_number=number,
        source_upload=upload,
        inspection_job=inspection_job,
        status=status,
        source_format="GeoJSON",
        source_checksum_sha256=str(number) * 64,
        inspection_result={},
        created_by=user,
        imported_at=timezone.now() if status == DatasetVersionStatus.READY else None,
    )


def _layer(
    *,
    vector: VectorDataset,
    version: DatasetVersion,
    status: str,
) -> VectorLayer:
    layer_id = uuid4()
    ready = status == VectorLayerStatus.READY
    return VectorLayer.objects.create(
        id=layer_id,
        vector_dataset=vector,
        version=version,
        ordinal=1,
        source_layer_name=f"roads_{version.version_number}",
        title=f"Roads {version.version_number}",
        status=status,
        source_driver="GeoJSON",
        source_crs="EPSG:4326",
        field_schema=[],
        geometry_type="MULTILINESTRING",
        db_schema=settings.DATASET_DB_SCHEMA if ready else "",
        db_table=f"v_{layer_id.hex}" if ready else "",
        tile_source_id=f"v_{layer_id.hex}" if ready else "",
        srid=4326 if ready else None,
    )


def _create_table(schema: str, table: str) -> None:
    quoted_schema = connection.ops.quote_name(schema)
    quoted_table = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        cursor.execute(f"CREATE TABLE {quoted_schema}.{quoted_table} (gx_fid bigint)")


def _drop_table(schema: str, table: str) -> None:
    quoted_schema = connection.ops.quote_name(schema)
    quoted_table = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {quoted_schema}.{quoted_table} CASCADE")


def _table_exists(schema: str, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"{schema}.{table}"])
        return cursor.fetchone()[0] is not None


@pytest.mark.django_db(transaction=True)
def test_dry_run_detects_orphans_and_protects_ready_history() -> None:
    user = _user("reconcile-history")
    dataset, vector = _dataset(user, "reconcile-history")
    first = _version(
        dataset=dataset,
        user=user,
        number=1,
        status=DatasetVersionStatus.READY,
    )
    second = _version(
        dataset=dataset,
        user=user,
        number=2,
        status=DatasetVersionStatus.READY,
    )
    first_layer = _layer(vector=vector, version=first, status=VectorLayerStatus.READY)
    second_layer = _layer(vector=vector, version=second, status=VectorLayerStatus.READY)
    dataset.current_version = second
    dataset.save(update_fields=("current_version", "updated_at"))

    orphan_final = f"v_{uuid4().hex}"
    orphan_staging = f"stg_{uuid4().hex}"
    managed = [
        (settings.DATASET_DB_SCHEMA, first_layer.db_table),
        (settings.DATASET_DB_SCHEMA, second_layer.db_table),
        (settings.DATASET_DB_SCHEMA, orphan_final),
        (settings.DATASET_STAGING_SCHEMA, orphan_staging),
    ]
    try:
        for schema, table in managed:
            _create_table(schema, table)
        report = reconcile_vector_imports(stale_after=timedelta(minutes=5))
        codes = {issue.code for issue in report.issues}
        assert "ORPHAN_FINAL_TABLE" in codes
        assert "ORPHAN_STAGING_TABLE" in codes
        assert (settings.DATASET_DB_SCHEMA, first_layer.db_table) in report.protected
        assert (settings.DATASET_DB_SCHEMA, second_layer.db_table) in report.protected
        assert _table_exists(settings.DATASET_DB_SCHEMA, orphan_final)
        assert _table_exists(settings.DATASET_STAGING_SCHEMA, orphan_staging)
    finally:
        for schema, table in managed:
            _drop_table(schema, table)


@pytest.mark.django_db(transaction=True)
def test_apply_drops_orphans_but_preserves_active_and_unmanaged_tables() -> None:
    user = _user("reconcile-active")
    dataset, vector = _dataset(user, "reconcile-active")
    version = _version(
        dataset=dataset,
        user=user,
        number=1,
        status=DatasetVersionStatus.IMPORTING,
    )
    layer = _layer(vector=vector, version=version, status=VectorLayerStatus.IMPORTING)
    Job.objects.create(
        created_by=user,
        resource=dataset.resource,
        job_type="vector-import",
        status=JobStatus.RUNNING,
        input_parameters={"dataset_version_id": str(version.id)},
        last_heartbeat_at=timezone.now(),
    )

    active_staging = f"stg_{layer.id.hex}"
    orphan_final = f"v_{uuid4().hex}"
    orphan_staging = f"stg_{uuid4().hex}"
    unmanaged = "manual_keep"
    tables = [
        (settings.DATASET_STAGING_SCHEMA, active_staging),
        (settings.DATASET_DB_SCHEMA, orphan_final),
        (settings.DATASET_STAGING_SCHEMA, orphan_staging),
        (settings.DATASET_DB_SCHEMA, unmanaged),
    ]
    try:
        for schema, table in tables:
            _create_table(schema, table)
        report = reconcile_vector_imports(
            stale_after=timedelta(minutes=5),
            apply=True,
            drop_orphans=True,
        )
        assert not _table_exists(settings.DATASET_DB_SCHEMA, orphan_final)
        assert not _table_exists(settings.DATASET_STAGING_SCHEMA, orphan_staging)
        assert _table_exists(settings.DATASET_STAGING_SCHEMA, active_staging)
        assert _table_exists(settings.DATASET_DB_SCHEMA, unmanaged)
        dropped = {
            (action["schema"], action["table"])
            for action in report.applied_actions
            if action["action"] == "DROP_TABLE"
        }
        assert (settings.DATASET_DB_SCHEMA, orphan_final) in dropped
        assert (settings.DATASET_STAGING_SCHEMA, orphan_staging) in dropped
    finally:
        for schema, table in tables:
            _drop_table(schema, table)


@pytest.mark.django_db(transaction=True)
def test_fail_stale_candidate_preserves_current_ready_version() -> None:
    user = _user("reconcile-stale")
    dataset, vector = _dataset(user, "reconcile-stale")
    current = _version(
        dataset=dataset,
        user=user,
        number=1,
        status=DatasetVersionStatus.READY,
    )
    current_layer = _layer(vector=vector, version=current, status=VectorLayerStatus.READY)
    candidate = _version(
        dataset=dataset,
        user=user,
        number=2,
        status=DatasetVersionStatus.IMPORTING,
    )
    candidate_layer = _layer(
        vector=vector,
        version=candidate,
        status=VectorLayerStatus.IMPORTING,
    )
    dataset.current_version = current
    dataset.save(update_fields=("current_version", "updated_at"))

    old = timezone.now() - timedelta(hours=3)
    DatasetVersion.objects.filter(pk=candidate.pk).update(created_at=old)
    VectorLayer.objects.filter(pk=candidate_layer.pk).update(updated_at=old)
    current_table = current_layer.db_table
    candidate_final = f"v_{candidate_layer.id.hex}"
    candidate_staging = f"stg_{candidate_layer.id.hex}"
    tables = [
        (settings.DATASET_DB_SCHEMA, current_table),
        (settings.DATASET_DB_SCHEMA, candidate_final),
        (settings.DATASET_STAGING_SCHEMA, candidate_staging),
    ]
    try:
        for schema, table in tables:
            _create_table(schema, table)
        report = reconcile_vector_imports(
            stale_after=timedelta(minutes=30),
            apply=True,
            fail_stale_versions=True,
        )
        candidate.refresh_from_db()
        candidate_layer.refresh_from_db()
        dataset.refresh_from_db()
        dataset.resource.refresh_from_db()
        assert candidate.status == DatasetVersionStatus.FAILED
        assert candidate_layer.status == VectorLayerStatus.FAILED
        assert candidate_layer.db_table == ""
        assert dataset.current_version_id == current.id
        assert dataset.status == DatasetStatus.READY
        assert dataset.resource.lifecycle_status == LifecycleStatus.READY
        assert _table_exists(settings.DATASET_DB_SCHEMA, current_table)
        assert not _table_exists(settings.DATASET_DB_SCHEMA, candidate_final)
        assert not _table_exists(settings.DATASET_STAGING_SCHEMA, candidate_staging)
        assert any(
            action["action"] == "MARK_STALE_VERSION_FAILED"
            for action in report.applied_actions
        )
    finally:
        for schema, table in tables:
            _drop_table(schema, table)


@pytest.mark.django_db(transaction=True)
def test_stale_active_job_is_report_only_even_in_apply_mode() -> None:
    user = _user("reconcile-stale-job")
    dataset, vector = _dataset(user, "reconcile-stale-job")
    version = _version(
        dataset=dataset,
        user=user,
        number=1,
        status=DatasetVersionStatus.IMPORTING,
    )
    layer = _layer(vector=vector, version=version, status=VectorLayerStatus.IMPORTING)
    old = timezone.now() - timedelta(hours=3)
    job = Job.objects.create(
        created_by=user,
        resource=dataset.resource,
        job_type="vector-import",
        status=JobStatus.RUNNING,
        input_parameters={"dataset_version_id": str(version.id)},
        last_heartbeat_at=old,
    )
    Job.objects.filter(pk=job.pk).update(updated_at=old)
    VectorLayer.objects.filter(pk=layer.pk).update(updated_at=old)
    staging = f"stg_{layer.id.hex}"
    try:
        _create_table(settings.DATASET_STAGING_SCHEMA, staging)
        report = reconcile_vector_imports(
            stale_after=timedelta(minutes=30),
            apply=True,
            drop_orphans=True,
            fail_stale_versions=True,
        )
        version.refresh_from_db()
        assert version.status == DatasetVersionStatus.IMPORTING
        assert _table_exists(settings.DATASET_STAGING_SCHEMA, staging)
        assert any(issue.code == "STALE_ACTIVE_IMPORT_JOB" for issue in report.issues)
        assert not report.applied_actions
    finally:
        _drop_table(settings.DATASET_STAGING_SCHEMA, staging)


@pytest.mark.django_db(transaction=True)
def test_ready_layer_missing_table_is_critical_and_not_mutated() -> None:
    user = _user("reconcile-missing")
    dataset, vector = _dataset(user, "reconcile-missing")
    version = _version(
        dataset=dataset,
        user=user,
        number=1,
        status=DatasetVersionStatus.READY,
    )
    layer = _layer(vector=vector, version=version, status=VectorLayerStatus.READY)
    dataset.current_version = version
    dataset.save(update_fields=("current_version", "updated_at"))

    report = reconcile_vector_imports(
        stale_after=timedelta(minutes=5),
        apply=True,
        drop_orphans=True,
        fail_stale_versions=True,
    )
    issue = next(item for item in report.issues if item.code == "READY_LAYER_TABLE_MISSING")
    assert issue.severity == "CRITICAL"
    layer.refresh_from_db()
    assert layer.status == VectorLayerStatus.READY
    assert layer.db_table
    assert not report.applied_actions


@pytest.mark.django_db
def test_command_requires_apply_for_mutations() -> None:
    with pytest.raises(CommandError, match="require --apply"):
        call_command("reconcile_vector_imports", "--drop-orphans")
