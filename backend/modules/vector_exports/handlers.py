from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job
from modules.jobs.registry import register_job_handler
from modules.jobs.services import JobCancellationRequested
from modules.object_storage.publication import PublishedObject, publish_file
from modules.object_storage.services import delete_object
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission

from .exporter import generate_vector_export, object_key_for
from .models import VectorExport, VectorExportStatus


@register_job_handler("vector-export")
def run_vector_export(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    export = _resolve_export(context, parameters)
    if export.status == VectorExportStatus.READY and export.object_key:
        return _result_payload(export)

    published: PublishedObject | None = None
    key = object_key_for(export)
    try:
        _mark_running(export)
        context.report_progress(8, "Validated vector export")
        with TemporaryDirectory(prefix=f"geoportalx-export-{export.id}-") as directory:
            generated = generate_vector_export(
                export=export,
                workspace=Path(directory),
                cancel_check=context.ensure_not_cancelled,
            )
            context.report_progress(70, "Generated vector export")
            context.ensure_not_cancelled()
            published = publish_file(
                path=generated.path,
                key=key,
                content_type=generated.content_type,
                metadata={
                    "vector-export-id": str(export.id),
                    "vector-layer-id": str(export.layer_id),
                    "created-by": str(export.created_by_id),
                },
            )
            context.report_progress(92, "Published export object")
            _mark_ready(
                export=export,
                published=published,
                filename=generated.filename,
            )
    except JobCancellationRequested:
        if published is not None:
            with suppress(Exception):
                delete_object(key=published.key)
        _mark_cancelled(export)
        raise
    except Exception as exc:
        if published is not None:
            with suppress(Exception):
                delete_object(key=published.key)
        _mark_failed(export, exc)
        raise
    return _result_payload(export)


def _resolve_export(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> VectorExport:
    raw_id = parameters.get("vector_export_id")
    try:
        export_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("vector_export_id must be a UUID") from exc
    try:
        export = VectorExport.objects.select_related(
            "created_by",
            "job",
            "layer",
            "layer__vector_dataset",
            "layer__vector_dataset__dataset",
            "layer__vector_dataset__dataset__resource",
        ).get(pk=export_id)
    except VectorExport.DoesNotExist as exc:
        raise ValueError("Vector export does not exist") from exc

    job = Job.objects.select_related("created_by", "resource").get(pk=context.job_id)
    resource = export.layer.vector_dataset.dataset.resource
    if export.job_id != job.id:
        raise PermissionError("Export job does not match the vector export")
    if job.created_by_id != export.created_by_id:
        raise PermissionError("Export job creator does not match the export owner")
    if job.resource_id != resource.id:
        raise PermissionError("Export job resource does not match the vector layer")
    if not has_resource_permission(job.created_by, resource, PermissionAction.DOWNLOAD):
        raise PermissionError("Export job creator cannot download this resource")
    return export


@transaction.atomic
def _mark_running(export: VectorExport) -> None:
    locked = VectorExport.objects.select_for_update().get(pk=export.pk)
    if locked.status not in {VectorExportStatus.PENDING, VectorExportStatus.RUNNING}:
        raise ValueError(f"Vector export cannot run from status {locked.status}")
    locked.status = VectorExportStatus.RUNNING
    locked.started_at = locked.started_at or timezone.now()
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save(
        update_fields=(
            "status",
            "started_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    export.status = locked.status
    export.started_at = locked.started_at


@transaction.atomic
def _mark_ready(
    *,
    export: VectorExport,
    published: PublishedObject,
    filename: str,
) -> None:
    locked = VectorExport.objects.select_for_update().get(pk=export.pk)
    if locked.status != VectorExportStatus.RUNNING:
        raise ValueError(f"Vector export cannot complete from status {locked.status}")
    now = timezone.now()
    locked.status = VectorExportStatus.READY
    locked.bucket = published.bucket
    locked.object_key = published.key
    locked.object_version_id = published.version_id
    locked.object_etag = published.etag
    locked.checksum_sha256 = published.checksum_sha256
    locked.content_type = published.content_type
    locked.result_filename = filename
    locked.result_size = published.size
    locked.expires_at = now + timedelta(
        seconds=max(int(settings.VECTOR_EXPORT_RETENTION_SECONDS), 1)
    )
    locked.completed_at = now
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save(
        update_fields=(
            "status",
            "bucket",
            "object_key",
            "object_version_id",
            "object_etag",
            "checksum_sha256",
            "content_type",
            "result_filename",
            "result_size",
            "expires_at",
            "completed_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    export.refresh_from_db()


@transaction.atomic
def _mark_cancelled(export: VectorExport) -> None:
    locked = VectorExport.objects.select_for_update().get(pk=export.pk)
    locked.status = VectorExportStatus.CANCELLED
    locked.completed_at = timezone.now()
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save(
        update_fields=(
            "status",
            "completed_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    export.status = locked.status
    export.completed_at = locked.completed_at


@transaction.atomic
def _mark_failed(export: VectorExport, exc: Exception) -> None:
    locked = VectorExport.objects.select_for_update().get(pk=export.pk)
    locked.status = VectorExportStatus.FAILED
    locked.failure_code = exc.__class__.__name__.upper()[:100]
    locked.failure_message = str(exc)
    locked.completed_at = timezone.now()
    locked.save(
        update_fields=(
            "status",
            "failure_code",
            "failure_message",
            "completed_at",
            "updated_at",
        )
    )
    export.status = locked.status
    export.failure_code = locked.failure_code
    export.failure_message = locked.failure_message
    export.completed_at = locked.completed_at


def _result_payload(export: VectorExport) -> dict[str, Any]:
    export.refresh_from_db()
    return {
        "vector_export_id": str(export.id),
        "vector_layer_id": str(export.layer_id),
        "format": export.export_format,
        "status": export.status,
        "bucket": export.bucket,
        "object_key": export.object_key,
        "filename": export.result_filename,
        "content_type": export.content_type,
        "size": export.result_size,
        "checksum_sha256": export.checksum_sha256,
        "expires_at": export.expires_at.isoformat() if export.expires_at else None,
    }
