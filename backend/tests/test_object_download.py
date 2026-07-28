import hashlib

from modules.object_storage import services


class FakeBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.chunk_size = None

    def iter_chunks(self, *, chunk_size: int):
        self.chunk_size = chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class FakeDownloadClient:
    def __init__(self, body: FakeBody, payload_size: int) -> None:
        self.body = body
        self.payload_size = payload_size
        self.parameters = None

    def get_object(self, **parameters):
        self.parameters = parameters
        return {
            "Body": self.body,
            "ContentLength": self.payload_size,
            "ETag": '"download-etag"',
            "VersionId": "version-1",
            "ContentType": "application/geo+json",
        }


def test_download_object_streams_hashes_and_atomically_publishes(tmp_path, monkeypatch) -> None:
    chunks = [b'{"type":', b'"FeatureCollection"}']
    payload = b"".join(chunks)
    body = FakeBody(chunks)
    client = FakeDownloadClient(body, len(payload))
    monkeypatch.setattr(services, "get_s3_client", lambda: client)
    destination = tmp_path / "source.geojson"

    downloaded = services.download_object(
        key="uploads/owner/upload/source.geojson",
        bucket="source-bucket",
        version_id="requested-version",
        destination=destination,
        chunk_size=4,
    )

    assert destination.read_bytes() == payload
    assert downloaded.path == destination
    assert downloaded.size == len(payload)
    assert downloaded.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert downloaded.etag == "download-etag"
    assert downloaded.version_id == "version-1"
    assert downloaded.content_type == "application/geo+json"
    assert client.parameters == {
        "Bucket": "source-bucket",
        "Key": "uploads/owner/upload/source.geojson",
        "VersionId": "requested-version",
    }
    assert body.chunk_size == 4
    assert body.closed is True
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob(".*.part")) == []


def test_download_object_removes_partial_file_on_truncation(tmp_path, monkeypatch) -> None:
    body = FakeBody([b"short"])
    client = FakeDownloadClient(body, payload_size=10)
    monkeypatch.setattr(services, "get_s3_client", lambda: client)
    destination = tmp_path / "source.tif"

    try:
        services.download_object(
            key="uploads/owner/upload/source.tif",
            destination=destination,
        )
    except services.ObjectStorageError as exc:
        assert "truncated" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected truncated download to fail")

    assert destination.exists() is False
    assert list(tmp_path.iterdir()) == []
    assert body.closed is True
