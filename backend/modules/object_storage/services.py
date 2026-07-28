from dataclasses import dataclass
from typing import Any

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


def ensure_bucket() -> None:
    """Create and configure the canonical bucket when it does not exist."""

    client = get_s3_client()
    bucket = settings.S3_BUCKET
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code not in {"404", "NoSuchBucket", "NotFound"} and status_code != 404:
            raise ObjectStorageError(f"Could not inspect bucket {bucket}") from exc
        parameters: dict[str, Any] = {"Bucket": bucket}
        if settings.S3_REGION != "us-east-1":
            parameters["CreateBucketConfiguration"] = {
                "LocationConstraint": settings.S3_REGION,
            }
        client.create_bucket(**parameters)

    configure_bucket_cors()
    configure_incomplete_upload_lifecycle()


def configure_bucket_cors() -> None:
    origins = list(settings.S3_CORS_ALLOWED_ORIGINS)
    if not origins:
        return
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


def configure_incomplete_upload_lifecycle() -> None:
    days = settings.S3_ABORT_INCOMPLETE_DAYS
    if days <= 0:
        return
    get_s3_client().put_bucket_lifecycle_configuration(
        Bucket=settings.S3_BUCKET,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "geoportalx-abort-incomplete-multipart-uploads",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "uploads/"},
                    "AbortIncompleteMultipartUpload": {
                        "DaysAfterInitiation": days,
                    },
                }
            ]
        },
    )


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


def delete_object(*, key: str) -> None:
    try:
        get_s3_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        raise ObjectStorageError("Could not delete object") from exc
