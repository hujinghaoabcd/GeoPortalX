import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from modules.object_storage import services
from modules.object_storage.client import clear_s3_client_cache
from modules.object_storage.publication import presign_download, publish_file


@pytest.mark.skipif(
    os.getenv("RUN_STORAGE_INTEGRATION") != "1",
    reason="requires a live S3-compatible endpoint",
)
def test_presigned_multipart_roundtrip() -> None:
    clear_s3_client_cache()
    services.ensure_bucket()
    payload = b"geoportalx-minio-integration"
    key = f"integration/{uuid4()}/roundtrip.bin"
    upload_id = services.initiate_multipart_upload(
        key=key,
        content_type="application/octet-stream",
    )

    try:
        url = services.presign_upload_part(
            key=key,
            upload_id=upload_id,
            part_number=1,
        )
        request = Request(url, data=payload, method="PUT")
        with urlopen(request, timeout=30) as response:  # noqa: S310
            etag = response.headers["ETag"]

        services.complete_multipart_upload(
            key=key,
            upload_id=upload_id,
            parts=[{"PartNumber": 1, "ETag": etag}],
        )
        stored = services.inspect_object(key=key)
        assert stored.size == len(payload)
        assert stored.content_type == "application/octet-stream"
    finally:
        try:
            services.delete_object(key=key)
        except services.ObjectStorageError:
            services.abort_multipart_upload(key=key, upload_id=upload_id)


@pytest.mark.skipif(
    os.getenv("RUN_STORAGE_INTEGRATION") != "1",
    reason="requires a live S3-compatible endpoint",
)
def test_generated_file_publication_and_signed_download() -> None:
    clear_s3_client_cache()
    services.ensure_bucket()
    payload = b'{"type":"FeatureCollection","features":[]}'
    key = f"exports/integration/{uuid4()}/result.geojson"

    try:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "result.geojson"
            path.write_bytes(payload)
            published = publish_file(
                path=path,
                key=key,
                content_type="application/geo+json",
                metadata={"test": "vector-export"},
            )
        assert published.size == len(payload)
        assert len(published.checksum_sha256) == 64

        url = presign_download(
            key=key,
            filename="roads.geojson",
            content_type="application/geo+json",
            expires_in=60,
        )
        with urlopen(url, timeout=30) as response:  # noqa: S310
            assert response.read() == payload
            assert "roads.geojson" in response.headers["Content-Disposition"]
    finally:
        services.delete_object(key=key)
