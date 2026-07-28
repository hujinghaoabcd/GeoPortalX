from typing import Any

from modules.jobs.context import JobExecutionContext
from modules.jobs.registry import register_job_handler
from modules.uploads.models import UploadSession

from .inputs import resolve_completed_upload
from .materialization import MaterializedUpload, materialize_completed_upload
from .raster import inspect_raster_dataset
from .vector import inspect_vector_dataset


@register_job_handler("vector-inspect")
def run_vector_inspection(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    upload = resolve_completed_upload(context, parameters)
    context.report_progress(10, "Validated completed vector upload")

    with materialize_completed_upload(upload) as materialized:
        context.ensure_not_cancelled()
        context.report_progress(30, "Materialized vector source")

        def report_layer(index: int, total: int, name: str) -> None:
            progress = 35 + int(index / max(total, 1) * 50)
            context.report_progress(min(progress, 85), f"Inspecting vector layer {name}")
            context.ensure_not_cancelled()

        inspection = inspect_vector_dataset(
            materialized.path,
            original_filename=upload.original_filename,
            progress=report_layer,
        )
        context.report_progress(92, "Vector inspection complete")
        return _result_payload(upload, materialized, inspection)


@register_job_handler("raster-inspect")
def run_raster_inspection(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    upload = resolve_completed_upload(context, parameters)
    context.report_progress(10, "Validated completed raster upload")

    with materialize_completed_upload(upload) as materialized:
        context.ensure_not_cancelled()
        context.report_progress(45, "Materialized raster source")
        inspection = inspect_raster_dataset(
            materialized.path,
            original_filename=upload.original_filename,
        )
        context.report_progress(92, "Raster inspection complete")
        return _result_payload(upload, materialized, inspection)


def _result_payload(
    upload: UploadSession,
    materialized: MaterializedUpload,
    inspection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "upload": {
            "id": str(upload.id),
            "original_filename": upload.original_filename,
            "content_type": upload.content_type,
            "size": materialized.size,
            "sha256": materialized.checksum_sha256,
            "resource_id": str(upload.resource_id) if upload.resource_id else None,
        },
        "inspection": inspection,
    }
