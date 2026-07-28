from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth
from pydantic import Field

from modules.permissions.models import PermissionAction
from modules.resources.selectors import resource_accessible_to

from .models import UploadSession, UploadStatus
from .selectors import upload_visible_to, uploads_visible_to
from .services import (
    UploadLifecycleError,
    abort_upload_session,
    complete_upload_session,
    create_upload_session,
    presign_part,
)

router = Router(auth=SessionAuth(), tags=["uploads"])


class UploadCreateIn(Schema):
    original_filename: str
    declared_size: int
    content_type: str = "application/octet-stream"
    checksum_sha256: str = ""
    resource_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UploadPartIn(Schema):
    part_number: int
    etag: str


class UploadCompleteIn(Schema):
    parts: list[UploadPartIn]


class UploadOut(Schema):
    id: UUID
    resource_id: UUID | None
    original_filename: str
    content_type: str
    declared_size: int
    checksum_sha256: str
    bucket: str
    object_key: str
    status: str
    part_size: int
    part_count: int
    actual_size: int | None
    object_etag: str
    object_version_id: str
    failure_code: str
    failure_message: str
    metadata: dict[str, Any]
    expires_at: datetime
    completed_at: datetime | None
    aborted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PresignedPartOut(Schema):
    upload_id: UUID
    part_number: int
    method: str
    url: str
    expires_at: datetime


@router.get("/", response=list[UploadOut])
def list_uploads(request, status: str | None = None):
    queryset = uploads_visible_to(request.auth)
    if status is not None:
        if status not in UploadStatus.values:
            raise HttpError(400, "Unknown upload status")
        queryset = queryset.filter(status=status)
    return queryset


@router.post("/", response={201: UploadOut})
def create_upload(request, payload: UploadCreateIn):
    resource = None
    if payload.resource_id is not None:
        resource = resource_accessible_to(
            request.auth,
            payload.resource_id,
            PermissionAction.EDIT,
        )
        if resource is None:
            raise HttpError(404, "Resource not found")
    try:
        session = create_upload_session(
            created_by=request.auth,
            original_filename=payload.original_filename,
            declared_size=payload.declared_size,
            content_type=payload.content_type,
            checksum_sha256=payload.checksum_sha256,
            resource=resource,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    except UploadLifecycleError as exc:
        raise HttpError(503, str(exc)) from exc
    return 201, session


@router.get("/{upload_id}", response=UploadOut)
def get_upload(request, upload_id: UUID):
    session = _get_upload(request.auth, upload_id)
    return session


@router.post("/{upload_id}/parts/{part_number}", response=PresignedPartOut)
def create_part_url(request, upload_id: UUID, part_number: int):
    session = _get_upload(request.auth, upload_id)
    try:
        url = presign_part(session=session, part_number=part_number)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    except UploadLifecycleError as exc:
        raise HttpError(409, str(exc)) from exc
    return {
        "upload_id": session.id,
        "part_number": part_number,
        "method": "PUT",
        "url": url,
        "expires_at": timezone.now()
        + timedelta(seconds=settings.S3_PRESIGNED_URL_EXPIRY),
    }


@router.post("/{upload_id}/complete", response=UploadOut)
def complete_upload(request, upload_id: UUID, payload: UploadCompleteIn):
    session = _get_upload(request.auth, upload_id)
    parts = [
        {"PartNumber": part.part_number, "ETag": part.etag}
        for part in payload.parts
    ]
    try:
        return complete_upload_session(session=session, parts=parts)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    except UploadLifecycleError as exc:
        raise HttpError(409, str(exc)) from exc


@router.post("/{upload_id}/abort", response=UploadOut)
def abort_upload(request, upload_id: UUID):
    session = _get_upload(request.auth, upload_id)
    try:
        return abort_upload_session(session=session)
    except UploadLifecycleError as exc:
        raise HttpError(409, str(exc)) from exc


def _get_upload(user, upload_id: UUID) -> UploadSession:
    session = upload_visible_to(user, upload_id)
    if session is None:
        raise HttpError(404, "Upload session not found")
    return session
