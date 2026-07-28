from contextlib import suppress
from typing import Any
from uuid import UUID

from django.contrib.gis.geos import Polygon
from django.db import transaction
from django.utils import timezone

from modules.dataset_inspection.materialization import materialize_completed_upload
from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job
from modules.jobs.registry import register_job_handler
from modules.jobs.services import JobCancellationRequested
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission
from modules.resources.models import LifecycleStatus

from .importer import drop_vector_layer_storage, import_vector_layer
from .models import (
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    VectorLayer,
    VectorLayerStatus,
)


@register_job_handler("vector-import")
def run_vector_import(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    version = _resolve_import_version(context, parameters)
    if version.status == DatasetVersionStatus.READY:
        return _result_payload(version)

    layers = list(
        version.vector_layers.select_related(
            "vector_dataset",
            "vector_dataset__dataset",
            "vector_dataset__dataset__resource",
        ).order_by("ordinal")
    )
    if not layers:
        raise ValueError("Dataset version has no vector layers to import")

    try:
        _prepare_import(version, layers)
        context.report_progress(8, "Validated dataset registration")
        with materialize_completed_upload(version.source_upload) as materialized:
            context.report_progress(18, "Materialized vector source")
            for index, layer in enumerate(layers, start=1):
                context.ensure_not_cancelled()
                _mark_layer_importing(layer)
                progress = 20 + int((index - 1) / len(layers) * 65)
                context.report_progress(progress, f"Importing vector layer {layer.title}")
                metadata = import_vector_layer(source=materialized.path, layer=layer)
                _mark_layer_ready(layer, metadata)
            context.report_progress(90, "Finalizing vector dataset")
            _mark_import_ready(version)
    except JobCancellationRequested:
        _mark_import_cancelled(version, layers)
        raise
    except Exception as exc:
        _mark_import_failed(version, layers, exc)
        raise
    return _result_payload(version)


def _resolve_import_version(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> DatasetVersion:
    raw_id = parameters.get("dataset_version_id")
    try:
        version_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("dataset_version_id must be a UUID") from exc
    try:
        version = DatasetVersion.objects.select_related(
            "dataset",
            "dataset__resource",
            "source_upload",
        ).get(pk=version_id)
    except DatasetVersion.DoesNotExist as exc:
        raise ValueError("Dataset version does not exist") from exc
    if version.dataset.kind != "VECTOR":
        raise ValueError("Only vector dataset versions can use vector-import")

    job = Job.objects.select_related("created_by", "resource").get(pk=context.job_id)
    if job.resource_id != version.dataset.resource_id:
        raise PermissionError("Import job resource does not match the dataset")
    if not has_resource_permission(job.created_by, version.dataset.resource, PermissionAction.EDIT):
        raise PermissionError("Import job creator cannot edit the dataset")
    if version.source_upload.created_by_id != job.created_by_id and not job.created_by.is_superuser:
        raise PermissionError("Import job creator does not own the source upload")
    return version


def _prepare_import(version: DatasetVersion, layers: list[VectorLayer]) -> None:
    for layer in layers:
        drop_vector_layer_storage(layer)
    with transaction.atomic():
        version.dataset.status = DatasetStatus.IMPORTING
        version.dataset.failure_code = ""
        version.dataset.failure_message = ""
        version.dataset.save(
            update_fields=("status", "failure_code", "failure_message", "updated_at")
        )
        version.status = DatasetVersionStatus.IMPORTING
        version.failure_code = ""
        version.failure_message = ""
        version.save(update_fields=("status", "failure_code", "failure_message"))
        version.dataset.resource.lifecycle_status = LifecycleStatus.PROCESSING
        version.dataset.resource.save(update_fields=("lifecycle_status", "updated_at"))
        VectorLayer.objects.filter(pk__in=[layer.pk for layer in layers]).update(
            status=VectorLayerStatus.REGISTERED,
            db_schema="",
            db_table="",
            tile_source_id="",
            quality_report={},
            field_statistics=[],
            failure_code="",
            failure_message="",
        )


def _mark_layer_importing(layer: VectorLayer) -> None:
    layer.status = VectorLayerStatus.IMPORTING
    layer.failure_code = ""
    layer.failure_message = ""
    layer.save(update_fields=("status", "failure_code", "failure_message", "updated_at"))


def _mark_layer_ready(layer: VectorLayer, metadata) -> None:
    layer.status = VectorLayerStatus.READY
    layer.db_schema = metadata.db_schema
    layer.db_table = metadata.db_table
    layer.geometry_column = metadata.geometry_column
    layer.geometry_type = metadata.geometry_type
    layer.srid = metadata.srid
    layer.feature_count = metadata.feature_count
    layer.field_schema = metadata.field_schema
    layer.extent = metadata.extent
    layer.quality_report = metadata.quality_report
    layer.field_statistics = metadata.field_statistics
    layer.tile_source_id = metadata.tile_source_id
    layer.min_zoom = metadata.min_zoom
    layer.max_zoom = metadata.max_zoom
    layer.save(
        update_fields=(
            "status",
            "db_schema",
            "db_table",
            "geometry_column",
            "geometry_type",
            "srid",
            "feature_count",
            "field_schema",
            "extent",
            "quality_report",
            "field_statistics",
            "tile_source_id",
            "min_zoom",
            "max_zoom",
            "updated_at",
        )
    )


@transaction.atomic
def _mark_import_ready(version: DatasetVersion) -> None:
    locked = DatasetVersion.objects.select_for_update().select_related(
        "dataset",
        "dataset__resource",
    ).get(pk=version.pk)
    layers = list(locked.vector_layers.all())
    if not layers or any(layer.status != VectorLayerStatus.READY for layer in layers):
        raise ValueError("All vector layers must be ready before finalization")
    locked.status = DatasetVersionStatus.READY
    locked.imported_at = timezone.now()
    locked.save(update_fields=("status", "imported_at", "failure_code", "failure_message"))

    dataset = locked.dataset
    dataset.status = DatasetStatus.READY
    dataset.current_version = locked
    dataset.failure_code = ""
    dataset.failure_message = ""
    dataset.save(
        update_fields=(
            "status",
            "current_version",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    dataset.vector.imported_layer_count = len(layers)
    dataset.vector.save(update_fields=("imported_layer_count",))

    extents = [layer.extent for layer in layers if layer.extent is not None]
    resource = dataset.resource
    resource.lifecycle_status = LifecycleStatus.READY
    resource.spatial_extent = _union_extents(extents)
    resource.save(update_fields=("lifecycle_status", "spatial_extent", "updated_at"))


def _mark_import_cancelled(version: DatasetVersion, layers: list[VectorLayer]) -> None:
    for layer in layers:
        with suppress(Exception):
            drop_vector_layer_storage(layer)
    with transaction.atomic():
        locked = DatasetVersion.objects.select_for_update().select_related(
            "dataset",
            "dataset__resource",
        ).get(pk=version.pk)
        locked.status = DatasetVersionStatus.REGISTERED
        locked.failure_code = ""
        locked.failure_message = ""
        locked.save(update_fields=("status", "failure_code", "failure_message"))
        locked.dataset.status = DatasetStatus.REGISTERED
        locked.dataset.failure_code = ""
        locked.dataset.failure_message = ""
        locked.dataset.save(
            update_fields=("status", "failure_code", "failure_message", "updated_at")
        )
        locked.dataset.resource.lifecycle_status = LifecycleStatus.DRAFT
        locked.dataset.resource.save(update_fields=("lifecycle_status", "updated_at"))
        VectorLayer.objects.filter(version=locked).update(
            status=VectorLayerStatus.REGISTERED,
            db_schema="",
            db_table="",
            tile_source_id="",
            quality_report={},
            field_statistics=[],
            failure_code="",
            failure_message="",
        )


def _mark_import_failed(
    version: DatasetVersion,
    layers: list[VectorLayer],
    exc: Exception,
) -> None:
    message = str(exc)
    error_code = exc.__class__.__name__.upper()[:100]
    for layer in layers:
        with suppress(Exception):
            drop_vector_layer_storage(layer)
    with transaction.atomic():
        locked = DatasetVersion.objects.select_for_update().select_related(
            "dataset",
            "dataset__resource",
        ).get(pk=version.pk)
        locked.status = DatasetVersionStatus.FAILED
        locked.failure_code = error_code
        locked.failure_message = message
        locked.save(update_fields=("status", "failure_code", "failure_message"))
        locked.dataset.status = DatasetStatus.FAILED
        locked.dataset.failure_code = error_code
        locked.dataset.failure_message = message
        locked.dataset.save(
            update_fields=("status", "failure_code", "failure_message", "updated_at")
        )
        locked.dataset.resource.lifecycle_status = LifecycleStatus.FAILED
        locked.dataset.resource.save(update_fields=("lifecycle_status", "updated_at"))
        VectorLayer.objects.filter(version=locked).update(
            status=VectorLayerStatus.FAILED,
            db_schema="",
            db_table="",
            tile_source_id="",
            quality_report={},
            field_statistics=[],
            failure_code=error_code,
            failure_message=message,
        )


def _union_extents(extents: list[Polygon]) -> Polygon | None:
    if not extents:
        return None
    min_x = min(extent.extent[0] for extent in extents)
    min_y = min(extent.extent[1] for extent in extents)
    max_x = max(extent.extent[2] for extent in extents)
    max_y = max(extent.extent[3] for extent in extents)
    polygon = Polygon.from_bbox((min_x, min_y, max_x, max_y))
    polygon.srid = 4326
    return polygon


def _result_payload(version: DatasetVersion) -> dict[str, Any]:
    version.refresh_from_db()
    layers = version.vector_layers.order_by("ordinal")
    return {
        "dataset_id": str(version.dataset_id),
        "resource_id": str(version.dataset.resource_id),
        "dataset_version_id": str(version.id),
        "version_number": version.version_number,
        "layers": [
            {
                "id": str(layer.id),
                "name": layer.source_layer_name,
                "status": layer.status,
                "schema": layer.db_schema,
                "table": layer.db_table,
                "geometry_column": layer.geometry_column,
                "geometry_type": layer.geometry_type,
                "srid": layer.srid,
                "feature_count": layer.feature_count,
                "tile_source_id": layer.tile_source_id,
                "quality_report": layer.quality_report,
            }
            for layer in layers
        ],
    }
