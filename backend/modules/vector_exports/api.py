from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError

from .services import (
    VectorExportUnavailable,
    VectorExportValidationError,
    cancel_vector_export,
    create_download_grant,
    create_vector_export,
    get_vector_export,
    list_vector_exports,
    serialize_vector_export,
)

router = Router(tags=["vector-exports"])


class CreateVectorExportPayload(Schema):
    layer_id: UUID
    export_format: str
    fields: list[str] | None = None
    bbox: list[float] | None = None


@router.post("/", response={202: dict})
def create_export(request, payload: CreateVectorExportPayload):
    actor = _require_authenticated(request)
    try:
        export = create_vector_export(
            actor=actor,
            layer_id=payload.layer_id,
            export_format=payload.export_format,
            fields=payload.fields,
            bbox=payload.bbox,
        )
    except LookupError as exc:
        raise HttpError(404, str(exc)) from exc
    except VectorExportValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    except VectorExportUnavailable as exc:
        raise HttpError(409, str(exc)) from exc
    return 202, serialize_vector_export(export)


@router.get("/", response=list[dict])
def list_exports(
    request,
    layer_id: UUID | None = None,
    export_status: str | None = None,
):
    actor = _require_authenticated(request)
    try:
        exports = list_vector_exports(
            actor=actor,
            layer_id=layer_id,
            status=export_status,
        )
    except VectorExportValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    return [serialize_vector_export(export) for export in exports]


@router.get("/{export_id}", response=dict)
def get_export(request, export_id: UUID):
    actor = _require_authenticated(request)
    export = get_vector_export(actor=actor, export_id=export_id)
    if export is None:
        raise HttpError(404, "Vector export not found")
    return serialize_vector_export(export)


@router.post("/{export_id}/cancel", response=dict)
def cancel_export(request, export_id: UUID):
    actor = _require_authenticated(request)
    try:
        export = cancel_vector_export(actor=actor, export_id=export_id)
    except LookupError as exc:
        raise HttpError(404, str(exc)) from exc
    except VectorExportUnavailable as exc:
        raise HttpError(409, str(exc)) from exc
    return serialize_vector_export(export)


@router.get("/{export_id}/download", response=dict)
def get_download(request, export_id: UUID):
    actor = _require_authenticated(request)
    try:
        grant = create_download_grant(actor=actor, export_id=export_id)
    except LookupError as exc:
        raise HttpError(404, str(exc)) from exc
    except VectorExportUnavailable as exc:
        raise HttpError(409, str(exc)) from exc
    return {
        "url": grant.url,
        "filename": grant.filename,
        "expires_at": grant.expires_at,
    }


def _require_authenticated(request):
    if not request.user.is_authenticated:
        raise HttpError(401, "Authentication required")
    return request.user
