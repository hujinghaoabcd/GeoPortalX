import re
from datetime import timedelta
from math import ceil
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.accounts.models import User
from modules.object_storage import services as storage
from modules.object_storage.keys import source_upload_key
from modules.resources.models import Resource

from .models import UploadSession, UploadStatus

_MIN_PART_SIZE = 5 * 1024 * 1024
_MAX_PARTS = 10_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UploadLifecycleError(RuntimeError):
    """Raised when an upload session cannot complete the requested transition."""


def calculate_part_size(declared_size: int) -> tuple[int, int]:
    """Return an S3-compatible part size and count for the declared object size."""

    configured_size = max(settings.S3_MULTIPART_PART_SIZE, _MIN_PART_SIZE)
    minimum_for_part_limit = ceil(declared_size / _MAX_PARTS)
    part_size = max(configured_size, minimum_for_part_limit)
    mebibyte = 1024 * 1024
    part_size = ceil(part_size / mebibyte) * mebibyte
    return part_size, ceil(declared_size / part_size)


def create_upload_session(
    *,
    created_by: User,
    original_filename: str,
    declared_size: int,
    content_type: str = "application/octet-stream",
    checksum_sha256: str = "",
    resource: Resource | None = None,
    metadata: dict[str, Any] | None = None,
) -> UploadSession:
    """Create a permanent session, then initiate its multipart upload."""

    filename = original_filename.strip()
    normalized_checksum = checksum_sha256.strip().lower()
    if not filename or "\x00" in filename or len(filename) > 512:
        raise ValueError("Upload filename is invalid")
    if not 0 < declared_size <= settings.S3_MAX_UPLOAD_SIZE:
        raise ValueError("Upload size is outside the configured limit")
    if normalized_checksum and not _SHA256.fullmatch(normalized_checksum):
        raise ValueError("checksum_sha256 must contain 64 lowercase hexadecimal characters")
    if not content_type or len(content_type) > 255:
        raise ValueError("Upload content type is invalid")

    part_size, part_count = calculate_part_size(declared_size)
    session = UploadSession(
        created_by=created_by,
        resource=resource,
        original_filename=filename,
        content_type=content_type,
        declared_size=declared_size,
        checksum_sha256=normalized_checksum,
        bucket=settings.S3_BUCKET,
        part_size=part_size,
        part_count=part_count,
        metadata=metadata or {},
        expires_at=timezone.now() + timedelta(seconds=settings.S3_UPLOAD_SESSION_EXPIRY),
    )
    session.object_key = source_upload_key(
        owner_id=created_by.id,
        upload_id=session.id,
        filename=filename,
    )
    session.save(force_insert=True)

    try:
        upload_id = storage.initiate_multipart_upload(
            key=session.object_key,
            content_type=session.content_type,
            metadata={"geoportalx-upload-id": str(session.id)},
        )
    except storage.ObjectStorageError as exc:
        _mark_failed(
            upload_id=session.id,
            code="UPLOAD_INIT_FAILED",
            message=str(exc),
        )
        raise UploadLifecycleError(str(exc)) from exc

    return _activate_session(upload_id=session.id, multipart_upload_id=upload_id)


@transaction.atomic
def _activate_session(*, upload_id: UUID, multipart_upload_id: str) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status != UploadStatus.INITIATING:
        raise UploadLifecycleError(f"Upload cannot activate from {session.status}")
    session.multipart_upload_id = multipart_upload_id
    session.status = UploadStatus.UPLOADING
    session.failure_code = ""
    session.failure_message = ""
    session.save(
        update_fields=(
            "multipart_upload_id",
            "status",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    return session


def presign_part(*, session: UploadSession, part_number: int) -> str:
    session = _uploadable_session(session.id)
    if not 1 <= part_number <= session.part_count:
        raise ValueError("Part number is outside this upload session")
    return storage.presign_upload_part(
        key=session.object_key,
        upload_id=session.multipart_upload_id,
        part_number=part_number,
    )


@transaction.atomic
def _uploadable_session(upload_id: UUID) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status != UploadStatus.UPLOADING:
        raise UploadLifecycleError(f"Upload is not accepting parts from {session.status}")
    if session.expires_at <= timezone.now():
        session.status = UploadStatus.EXPIRED
        session.failure_code = "UPLOAD_EXPIRED"
        session.failure_message = "The upload session expired"
        session.save(
            update_fields=(
                "status",
                "failure_code",
                "failure_message",
                "updated_at",
            )
        )
        raise UploadLifecycleError("The upload session expired")
    return session


def complete_upload_session(
    *,
    session: UploadSession,
    parts: list[dict[str, Any]],
) -> UploadSession:
    normalized_parts = _validate_parts(parts=parts, expected_count=session.part_count)
    reserved = _reserve_completion(upload_id=session.id)
    try:
        response = storage.complete_multipart_upload(
            key=reserved.object_key,
            upload_id=reserved.multipart_upload_id,
            parts=normalized_parts,
        )
        stored_object = storage.inspect_object(key=reserved.object_key)
    except storage.ObjectStorageError as exc:
        _restore_uploading(
            upload_id=reserved.id,
            code="UPLOAD_COMPLETE_FAILED",
            message=str(exc),
        )
        raise UploadLifecycleError(str(exc)) from exc

    if stored_object.size != reserved.declared_size:
        try:
            storage.delete_object(key=reserved.object_key)
        finally:
            _mark_failed(
                upload_id=reserved.id,
                code="UPLOAD_SIZE_MISMATCH",
                message=(
                    f"Declared {reserved.declared_size} bytes but stored "
                    f"{stored_object.size} bytes"
                ),
            )
        raise UploadLifecycleError("Uploaded object size does not match the declared size")

    return _finalize_completion(
        upload_id=reserved.id,
        parts=normalized_parts,
        actual_size=stored_object.size,
        etag=stored_object.etag or str(response.get("ETag", "")).strip('"'),
        version_id=stored_object.version_id or str(response.get("VersionId", "")),
    )


def _validate_parts(
    *,
    parts: list[dict[str, Any]],
    expected_count: int,
) -> list[dict[str, Any]]:
    if len(parts) != expected_count:
        raise ValueError("All upload parts must be supplied when completing the upload")
    normalized: list[dict[str, Any]] = []
    for item in parts:
        try:
            part_number = int(item["PartNumber"])
            etag = str(item["ETag"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each part requires PartNumber and ETag") from exc
        if not etag or len(etag) > 255:
            raise ValueError("Upload part ETag is invalid")
        normalized.append({"PartNumber": part_number, "ETag": etag})
    normalized.sort(key=lambda item: item["PartNumber"])
    expected_numbers = list(range(1, expected_count + 1))
    if [item["PartNumber"] for item in normalized] != expected_numbers:
        raise ValueError("Upload parts must be unique and contiguous from 1")
    return normalized


@transaction.atomic
def _reserve_completion(*, upload_id: UUID) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status != UploadStatus.UPLOADING:
        raise UploadLifecycleError(f"Upload cannot complete from {session.status}")
    if session.expires_at <= timezone.now():
        raise UploadLifecycleError("The upload session expired")
    session.status = UploadStatus.COMPLETING
    session.save(update_fields=("status", "updated_at"))
    return session


@transaction.atomic
def _restore_uploading(*, upload_id: UUID, code: str, message: str) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status == UploadStatus.COMPLETING:
        session.status = UploadStatus.UPLOADING
        session.failure_code = code
        session.failure_message = message
        session.save(
            update_fields=(
                "status",
                "failure_code",
                "failure_message",
                "updated_at",
            )
        )
    return session


@transaction.atomic
def _finalize_completion(
    *,
    upload_id: UUID,
    parts: list[dict[str, Any]],
    actual_size: int,
    etag: str,
    version_id: str,
) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status != UploadStatus.COMPLETING:
        raise UploadLifecycleError(f"Upload cannot finalize from {session.status}")
    session.status = UploadStatus.COMPLETED
    session.completed_parts = parts
    session.actual_size = actual_size
    session.object_etag = etag
    session.object_version_id = version_id
    session.completed_at = timezone.now()
    session.failure_code = ""
    session.failure_message = ""
    session.save(
        update_fields=(
            "status",
            "completed_parts",
            "actual_size",
            "object_etag",
            "object_version_id",
            "completed_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    return session


def abort_upload_session(*, session: UploadSession) -> UploadSession:
    reserved = _reserve_abort(upload_id=session.id)
    if reserved.status in {UploadStatus.ABORTED, UploadStatus.COMPLETED}:
        return reserved
    try:
        storage.abort_multipart_upload(
            key=reserved.object_key,
            upload_id=reserved.multipart_upload_id,
        )
    except storage.ObjectStorageError as exc:
        _restore_uploading(
            upload_id=reserved.id,
            code="UPLOAD_ABORT_FAILED",
            message=str(exc),
        )
        raise UploadLifecycleError(str(exc)) from exc
    return _finalize_abort(upload_id=reserved.id)


@transaction.atomic
def _reserve_abort(*, upload_id: UUID) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status in {UploadStatus.ABORTED, UploadStatus.COMPLETED}:
        return session
    if session.status not in {UploadStatus.UPLOADING, UploadStatus.FAILED, UploadStatus.EXPIRED}:
        raise UploadLifecycleError(f"Upload cannot abort from {session.status}")
    if not session.multipart_upload_id:
        session.status = UploadStatus.ABORTED
        session.aborted_at = timezone.now()
        session.save(update_fields=("status", "aborted_at", "updated_at"))
        return session
    session.status = UploadStatus.ABORTING
    session.save(update_fields=("status", "updated_at"))
    return session


@transaction.atomic
def _finalize_abort(*, upload_id: UUID) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    if session.status != UploadStatus.ABORTING:
        raise UploadLifecycleError(f"Upload cannot finalize abort from {session.status}")
    session.status = UploadStatus.ABORTED
    session.aborted_at = timezone.now()
    session.failure_code = ""
    session.failure_message = ""
    session.save(
        update_fields=(
            "status",
            "aborted_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    return session


@transaction.atomic
def _mark_failed(*, upload_id: UUID, code: str, message: str) -> UploadSession:
    session = UploadSession.objects.select_for_update().get(pk=upload_id)
    session.status = UploadStatus.FAILED
    session.failure_code = code
    session.failure_message = message
    session.save(
        update_fields=(
            "status",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )
    return session
