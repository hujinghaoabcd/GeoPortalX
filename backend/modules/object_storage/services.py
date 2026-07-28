import hashlib
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError
from django.conf import settings

from .client import get_s3_client


class ObjectStorageError(RuntimeError):
    """Raised when an S3-compatible operation cannot be completed."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    size: int
    etag: str
    version_id: str
    content_type: str


@dataclass(frozen=True, slots=True)
class DownloadedObject:
    bucket: str
    key: str
    path: Path
    size: int
    etag: str
    version_id: str
    content_type: str
    checksum_sha256: str


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def ensure_bucket() -> None:
    """Create and configure the canonical bucket when it does not exist."""

    client = get_s3_client()
    bucket = settings.S3_BUCKET
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        error_code = _client_error_code(exc)
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code not in {"404", "NoSuchBucket", "NotFound"} and status_code != 404:
            raise ObjectStorageError(f"Could not inspect bucket {bucket}") from exc
        parameters: dict[str, Any] = {"Bucket": bucket}
        if settings.S3_REGION != "us-east-1":
            parameters["CreateBucketConfiguration"] = {
                "LocationConstraint": settings.S3_REGION,
            }
        try:
            client.create_bucket(**parameters)
        except ClientError as create_exc:
            raise ObjectStorageError(f"Could not create bucket {bucket}") from create_exc

    configure_bucket_cors()
    configure_incomplete_upload_lifecycle()


def configure_bucket_cors() -> bool:
    """Configure bucket CORS when supported by the selected S3 provider.

    Some S3-compatible providers, including community MinIO releases, expose CORS
    through a server-level setting instead of ``PutBucketCors``. In that case the
    provider returns ``NotImplemented`` and bucket bootstrap continues. The return
    value tells callers whether bucket-level CORS was applied.
    """

    origins = list(settings.S3_CORS_ALLOWED_ORIGINS)
    if not origins:
        return False
    try:
        get_s3_client().put_bucket_cors(
            Bucket=settings.S3_BUCKET,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedHeaders": ["*"],
                        "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
                        "AllowedOrigins": origins,
                        "ExposeHeaders": ["ETag", "x-amz-checksum-sha256"],
                        "MaxAgeSeconds": 3600,
                    }
                ]
            },
        )
    except ClientError as exc:
        if _client_error_code(exc) in {"NotImplemented", "NotSupported"}:
            return False
        raise ObjectStorageError("Could not configure bucket CORS") from exc
    return True


def configure_incomplete_upload_lifecycle() -> bool:
    """Configure automatic multipart cleanup when the provider supports the API.

    AWS S3 errors remain fatal. For an explicitly configured S3-compatible endpoint,
    known compatibility responses allow bootstrap to continue so the deployment can
    use its provider-level stale-upload cleanup setting instead.
    """

    days = settings.S3_ABORT_INCOMPLETE_DAYS
    if days <= 0:
        return False
    try:
        get_s3_client().put_bucket_lifecycle_configuration(
            Bucket=settings.S3_BUCKET,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "geoportalx-abort-incomplete-multipart-uploads",
                        "Status": "Enabled",
                        "Prefix": "uploads/",
                        "AbortIncompleteMultipartUpload": {
                            "DaysAfterInitiation": days,
                        },
                    }
                ]
            },
        )
    except ClientError as exc:
        compatibility_errors = {"InvalidArgument", "NotImplemented", "NotSupported"}
        if settings.S3_ENDPOINT_URL and _client_error_code(exc) in compatibility_errors:
            return False
        raise ObjectStorageError("Could not configure multipart cleanup lifecycle") from exc
    return True


def initiate_multipart_upload(
    *,
    key: str,
    content_type: str,
    metadata: dict[str, str] | None = None,
) -> str:
    parameters: dict[str, Any] = {
        "Bucket": settings.S3_BUCKET,
        "Key": key,
        "ContentType": content_type,
        "Metadata": metadata or {},
    }
    if settings.S3_SERVER_SIDE_ENCRYPTION:
        parameters["ServerSideEncryption"] = settings.S3_SERVER_SIDE_ENCRYPTION
    try:
        response = get_s3_client().create_multipart_upload(**parameters)
        return str(response["UploadId"])
    except (ClientError, KeyError) as exc:
        raise ObjectStorageError("Could not initiate multipart upload") from exc


def presign_upload_part(
    *,
    key: str,
    upload_id: str,
    part_number: int,
) -> str:
    try:
        return get_s3_client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRY,
            HttpMethod="PUT",
        )
    except ClientError as exc:
        raise ObjectStorageError("Could not sign multipart upload part") from exc


def complete_multipart_upload(
    *,
    key: str,
    upload_id: str,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return get_s3_client().complete_multipart_upload(
            Bucket=settings.S3_BUCKET,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except ClientError as exc:
        raise ObjectStorageError("Could not complete multipart upload") from exc


def abort_multipart_upload(*, key: str, upload_id: str) -> None:
    try:
        get_s3_client().abort_multipart_upload(
            Bucket=settings.S3_BUCKET,
            Key=key,
            UploadId=upload_id,
        )
    except ClientError as exc:
        raise ObjectStorageError("Could not abort multipart upload") from exc


def inspect_object(*, key: str) -> StoredObject:
    try:
        response = get_s3_client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        raise ObjectStorageError("Could not inspect uploaded object") from exc
    return StoredObject(
        bucket=settings.S3_BUCKET,
        key=key,
        size=int(response["ContentLength"]),
        etag=str(response.get("ETag", "")).strip('"'),
        version_id=str(response.get("VersionId", "")),
        content_type=str(response.get("ContentType", "application/octet-stream")),
    )


def download_object(
    *,
    key: str,
    destination: Path,
    bucket: str | None = None,
    version_id: str = "",
    chunk_size: int = 8 * 1024 * 1024,
) -> DownloadedObject:
    """Stream an object into a temporary file and atomically publish it locally."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    target_bucket = bucket or settings.S3_BUCKET
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    parameters: dict[str, Any] = {"Bucket": target_bucket, "Key": key}
    if version_id:
        parameters["VersionId"] = version_id

    response: dict[str, Any] | None = None
    body = None
    try:
        response = get_s3_client().get_object(**parameters)
        body = response["Body"]
        digest = hashlib.sha256()
        bytes_written = 0
        with temporary.open("xb") as stream:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if not chunk:
                    continue
                stream.write(chunk)
                digest.update(chunk)
                bytes_written += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())

        expected_size = int(response["ContentLength"])
        if bytes_written != expected_size:
            raise ObjectStorageError(
                f"Object download was truncated: expected {expected_size}, got {bytes_written}"
            )
        os.replace(temporary, destination)
        return DownloadedObject(
            bucket=target_bucket,
            key=key,
            path=destination,
            size=bytes_written,
            etag=str(response.get("ETag", "")).strip('"'),
            version_id=str(response.get("VersionId", version_id)),
            content_type=str(response.get("ContentType", "application/octet-stream")),
            checksum_sha256=digest.hexdigest(),
        )
    except ClientError as exc:
        _remove_temporary_file(temporary)
        raise ObjectStorageError("Could not download object") from exc
    except ObjectStorageError:
        _remove_temporary_file(temporary)
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        _remove_temporary_file(temporary)
        raise ObjectStorageError("Could not materialize object") from exc
    finally:
        if body is not None:
            body.close()


def delete_object(*, key: str) -> None:
    try:
        get_s3_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        raise ObjectStorageError("Could not delete object") from exc


def _remove_temporary_file(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)
