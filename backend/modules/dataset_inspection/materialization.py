from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from modules.object_storage.keys import safe_extension
from modules.object_storage.services import ObjectStorageError, download_object
from modules.uploads.models import UploadSession, UploadStatus

from .exceptions import UploadMaterializationError


@dataclass(frozen=True, slots=True)
class MaterializedUpload:
    upload_id: str
    path: Path
    size: int
    checksum_sha256: str
    content_type: str


@contextmanager
def materialize_completed_upload(upload: UploadSession) -> Iterator[MaterializedUpload]:
    """Download one completed upload into an isolated temporary worker workspace."""

    if upload.status != UploadStatus.COMPLETED:
        raise UploadMaterializationError(
            f"Upload {upload.id} is not completed (status={upload.status})"
        )

    suffix = safe_extension(upload.original_filename)
    with TemporaryDirectory(prefix=f"geoportalx-upload-{upload.id}-") as workspace:
        destination = Path(workspace) / f"source{suffix}"
        try:
            downloaded = download_object(
                key=upload.object_key,
                destination=destination,
                bucket=upload.bucket,
                version_id=upload.object_version_id,
            )
        except ObjectStorageError as exc:
            raise UploadMaterializationError(str(exc)) from exc

        expected_size = upload.actual_size or upload.declared_size
        if downloaded.size != expected_size:
            raise UploadMaterializationError(
                f"Stored object size changed: expected {expected_size}, got {downloaded.size}"
            )
        if upload.checksum_sha256 and downloaded.checksum_sha256 != upload.checksum_sha256:
            raise UploadMaterializationError("Uploaded object SHA-256 does not match the declaration")

        yield MaterializedUpload(
            upload_id=str(upload.id),
            path=destination,
            size=downloaded.size,
            checksum_sha256=downloaded.checksum_sha256,
            content_type=downloaded.content_type,
        )
