import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from boto3.s3.transfer import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .client import get_s3_client
from .services import ObjectStorageError


@dataclass(frozen=True, slots=True)
class PublishedObject:
    bucket: str
    key: str
    size: int
    etag: str
    version_id: str
    content_type: str
    checksum_sha256: str


def publish_file(
    *,
    path: Path,
    key: str,
    content_type: str,
    metadata: dict[str, str] | None = None,
) -> PublishedObject:
    source = Path(path)
    if not source.is_file():
        raise ObjectStorageError("Published file does not exist")
    size = source.stat().st_size
    if size <= 0:
        raise ObjectStorageError("Published file is empty")

    checksum = _sha256(source)
    extra_args: dict[str, object] = {
        "ContentType": content_type,
        "Metadata": {
            **(metadata or {}),
            "sha256": checksum,
        },
    }
    if settings.S3_SERVER_SIDE_ENCRYPTION:
        extra_args["ServerSideEncryption"] = settings.S3_SERVER_SIDE_ENCRYPTION

    client = get_s3_client()
    try:
        client.upload_file(
            str(source),
            settings.S3_BUCKET,
            key,
            ExtraArgs=extra_args,
        )
        response = client.head_object(Bucket=settings.S3_BUCKET, Key=key)
    except (ClientError, BotoCoreError, S3UploadFailedError, OSError) as exc:
        raise ObjectStorageError("Could not publish generated file") from exc

    stored_size = int(response["ContentLength"])
    if stored_size != size:
        raise ObjectStorageError(
            f"Published object size mismatch: expected {size}, got {stored_size}"
        )
    return PublishedObject(
        bucket=settings.S3_BUCKET,
        key=key,
        size=stored_size,
        etag=str(response.get("ETag", "")).strip('"'),
        version_id=str(response.get("VersionId", "")),
        content_type=str(response.get("ContentType", content_type)),
        checksum_sha256=checksum,
    )


def presign_download(
    *,
    key: str,
    filename: str,
    content_type: str,
    expires_in: int,
    bucket: str = "",
    version_id: str = "",
) -> str:
    if expires_in <= 0:
        raise ValueError("expires_in must be positive")
    safe_name = _safe_download_filename(filename)
    disposition = (
        f'attachment; filename="{safe_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    parameters: dict[str, str] = {
        "Bucket": bucket or settings.S3_BUCKET,
        "Key": key,
        "ResponseContentDisposition": disposition,
        "ResponseContentType": content_type,
    }
    if version_id:
        parameters["VersionId"] = version_id
    try:
        return get_s3_client().generate_presigned_url(
            "get_object",
            Params=parameters,
            ExpiresIn=expires_in,
            HttpMethod="GET",
        )
    except (ClientError, BotoCoreError) as exc:
        raise ObjectStorageError("Could not sign object download") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_download_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "_").replace('"', "'")
    cleaned = cleaned.replace("\r", "").replace("\n", "")
    ascii_name = cleaned.encode("ascii", errors="ignore").decode("ascii")
    return ascii_name or "download"
