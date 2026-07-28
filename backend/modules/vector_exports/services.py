import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.models import VectorLayer, VectorLayerStatus
from modules.jobs.models import JobStatus
from modules.jobs.services import create_and_dispatch_job, request_job_cancellation
from modules.object_storage.publication import presign_download
from modules.vector_tiles.selectors import vector_layer_accessible_to

from .models import VectorExport, VectorExportFormat, VectorExportStatus


class VectorExportValidationError(ValueError):
    """Raised when a vector export request is invalid."""


class VectorExportUnavailable(RuntimeError):
    """Raised when an export is not ready for the requested operation."""


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    url: str
    filename: str
    expires_at: Any


@transaction.atomic
def create_vector_export(
    *,
    actor: User,
    layer_id: UUID,
    export_format: str,
    fields: list[str] | None = None,
    bbox: list[float] | None = None,
) -> VectorExport:
    layer = vector_layer_accessible_to(actor, layer_id)
    if layer is None:
        raise LookupError("Vector layer not found")
    if layer.status != VectorLayerStatus.READY:
        raise VectorExportUnavailable("Vector layer is not ready")
    if layer.srid != 4326:
        raise VectorExportUnavailable("Vector export currently requires EPSG:4326")
    if export_format not in VectorExportFormat.values:
        raise VectorExportValidationError("Unsupported vector export format")

    active_limit = max(int(settings.VECTOR_EXPORT_MAX_ACTIVE_PER_USER), 1)
    active_count = VectorExport.objects.filter(
        created_by=actor,
        status__in=(VectorExportStatus.PENDING, VectorExportStatus.RUNNING),
    ).count()
    if active_count >= active_limit:
        raise VectorExportUnavailable(
            f"At most {active_limit} vector exports may be active per user"
        )

    selected_fields = _validate_fields(layer, fields)
    normalized_bbox = _validate_bbox(bbox)
    export = VectorExport.objects.create(
        layer=layer,
        created_by=actor,
        export_format=export_format,
        selected_fields=selected_fields,
        bbox=normalized_bbox,
    )
    job = create_and_dispatch_job(
        created_by=actor,
        job_type="vector-export",
        input_parameters={"vector_export_id": str(export.id)},
        resource=layer.vector_dataset.dataset.resource,
        queue="vector",
        max_retries=0,
    )
    export.job = job
    export.save(update_fields=("job", "updated_at"))
    return export


def list_vector_exports(
    *,
    actor: User,
    layer_id: UUID | None = None,
    status: str | None = None,
) -> list[VectorExport]:
    queryset = VectorExport.objects.select_related(
        "job",
        "layer",
        "layer__vector_dataset",
        "layer__vector_dataset__dataset",
        "layer__vector_dataset__dataset__resource",
    )
    if not actor.is_superuser:
        queryset = queryset.filter(created_by=actor)
    if layer_id is not None:
        queryset = queryset.filter(layer_id=layer_id)
    if status is not None:
        if status not in VectorExportStatus.values:
            raise VectorExportValidationError("Unknown vector export status")
        queryset = queryset.filter(status=status)

    exports: list[VectorExport] = []
    for export in queryset[:100]:
        if vector_layer_accessible_to(actor, export.layer_id) is None:
            continue
        exports.append(synchronize_export_status(export))
    return exports


def get_vector_export(*, actor: User, export_id: UUID) -> VectorExport | None:
    queryset = VectorExport.objects.select_related(
        "job",
        "layer",
        "layer__vector_dataset",
        "layer__vector_dataset__dataset",
        "layer__vector_dataset__dataset__resource",
    )
    if not actor.is_superuser:
        queryset = queryset.filter(created_by=actor)
    export = queryset.filter(pk=export_id).first()
    if export is None:
        return None
    if vector_layer_accessible_to(actor, export.layer_id) is None:
        return None
    return synchronize_export_status(export)


def synchronize_export_status(export: VectorExport) -> VectorExport:
    now = timezone.now()
    updates: list[str] = []
    if (
        export.status == VectorExportStatus.READY
        and export.expires_at is not None
        and export.expires_at <= now
    ):
        export.status = VectorExportStatus.EXPIRED
        updates.append("status")

    job = export.job
    if job is not None and export.status in {
        VectorExportStatus.PENDING,
        VectorExportStatus.RUNNING,
    }:
        if job.status == JobStatus.RUNNING and export.status != VectorExportStatus.RUNNING:
            export.status = VectorExportStatus.RUNNING
            export.started_at = export.started_at or job.started_at or now
            updates.extend(("status", "started_at"))
        elif job.status == JobStatus.CANCELLED:
            export.status = VectorExportStatus.CANCELLED
            export.completed_at = export.completed_at or job.finished_at or now
            updates.extend(("status", "completed_at"))
        elif job.status == JobStatus.FAILED:
            export.status = VectorExportStatus.FAILED
            export.failure_code = job.error_code or "JOB_FAILED"
            export.failure_message = job.error_message
            export.completed_at = export.completed_at or job.finished_at or now
            updates.extend(
                ("status", "failure_code", "failure_message", "completed_at")
            )
        elif job.status == JobStatus.SUCCEEDED and not export.object_key:
            export.status = VectorExportStatus.FAILED
            export.failure_code = "EXPORT_RESULT_MISSING"
            export.failure_message = "Export job completed without a published object"
            export.completed_at = export.completed_at or job.finished_at or now
            updates.extend(
                ("status", "failure_code", "failure_message", "completed_at")
            )

    if updates:
        export.save(update_fields=tuple(dict.fromkeys((*updates, "updated_at"))))
    return export


def create_download_grant(*, actor: User, export_id: UUID) -> DownloadGrant:
    export = get_vector_export(actor=actor, export_id=export_id)
    if export is None:
        raise LookupError("Vector export not found")
    if export.status != VectorExportStatus.READY:
        raise VectorExportUnavailable("Vector export is not ready")
    if not export.object_key or not export.result_filename or not export.content_type:
        raise VectorExportUnavailable("Vector export object metadata is incomplete")
    if export.expires_at is None:
        raise VectorExportUnavailable("Vector export does not have an expiry time")

    remaining = int((export.expires_at - timezone.now()).total_seconds())
    if remaining <= 0:
        export.status = VectorExportStatus.EXPIRED
        export.save(update_fields=("status", "updated_at"))
        raise VectorExportUnavailable("Vector export has expired")
    expires_in = min(max(int(settings.S3_PRESIGNED_URL_EXPIRY), 1), remaining)
    signed_at = timezone.now()
    url = presign_download(
        key=export.object_key,
        filename=export.result_filename,
        content_type=export.content_type,
        expires_in=expires_in,
        bucket=export.bucket,
        version_id=export.object_version_id,
    )
    return DownloadGrant(
        url=url,
        filename=export.result_filename,
        expires_at=signed_at + timedelta(seconds=expires_in),
    )


def cancel_vector_export(*, actor: User, export_id: UUID) -> VectorExport:
    export = get_vector_export(actor=actor, export_id=export_id)
    if export is None:
        raise LookupError("Vector export not found")
    if export.job is None:
        raise VectorExportUnavailable("Vector export has no execution job")
    job = request_job_cancellation(job=export.job, requested_by=actor)
    export.job = job
    return synchronize_export_status(export)


def serialize_vector_export(export: VectorExport) -> dict[str, Any]:
    return {
        "id": str(export.id),
        "layer_id": str(export.layer_id),
        "resource_id": str(export.layer.vector_dataset.dataset.resource_id),
        "job_id": str(export.job_id) if export.job_id else None,
        "format": export.export_format,
        "status": export.status,
        "selected_fields": export.selected_fields,
        "bbox": export.bbox,
        "result_filename": export.result_filename or None,
        "result_size": export.result_size,
        "checksum_sha256": export.checksum_sha256 or None,
        "content_type": export.content_type or None,
        "failure_code": export.failure_code or None,
        "failure_message": export.failure_message or None,
        "expires_at": export.expires_at,
        "created_at": export.created_at,
        "started_at": export.started_at,
        "completed_at": export.completed_at,
    }


def _validate_fields(layer: VectorLayer, fields: list[str] | None) -> list[str]:
    available = [
        name
        for field in layer.field_schema
        if (name := str(field.get("name", "")))
        and name not in {"gx_fid", layer.geometry_column}
    ]
    selected = available if fields is None else list(dict.fromkeys(fields))
    maximum = max(int(settings.VECTOR_EXPORT_MAX_FIELDS), 1)
    if len(selected) > maximum:
        raise VectorExportValidationError(f"At most {maximum} fields may be exported")
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise VectorExportValidationError(
            f"Unknown or unavailable fields: {', '.join(unknown)}"
        )
    return selected


def _validate_bbox(bbox: list[float] | None) -> list[float]:
    if bbox is None or bbox == []:
        return []
    if len(bbox) != 4:
        raise VectorExportValidationError("bbox must contain four coordinates")
    values = [float(value) for value in bbox]
    if not all(math.isfinite(value) for value in values):
        raise VectorExportValidationError("bbox coordinates must be finite")
    min_x, min_y, max_x, max_y = values
    if not -180 <= min_x <= 180 or not -180 <= max_x <= 180:
        raise VectorExportValidationError("bbox longitude must be between -180 and 180")
    if not -90 <= min_y <= 90 or not -90 <= max_y <= 90:
        raise VectorExportValidationError("bbox latitude must be between -90 and 90")
    if min_x >= max_x or min_y >= max_y:
        raise VectorExportValidationError("bbox minimums must be smaller than maximums")
    return values
