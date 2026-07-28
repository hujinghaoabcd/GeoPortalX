from botocore.exceptions import ClientError

from modules.object_storage import services
from modules.object_storage.keys import safe_extension


class FakeS3Client:
    def __init__(self) -> None:
        self.created = []
        self.cors = []
        self.lifecycle = []

    def head_bucket(self, **kwargs):
        raise ClientError(
            {
                "Error": {"Code": "404", "Message": "Not Found"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadBucket",
        )

    def create_bucket(self, **kwargs):
        self.created.append(kwargs)

    def put_bucket_cors(self, **kwargs):
        self.cors.append(kwargs)

    def put_bucket_lifecycle_configuration(self, **kwargs):
        self.lifecycle.append(kwargs)


class CorsUnsupportedClient(FakeS3Client):
    def put_bucket_cors(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "NotImplemented",
                    "Message": "Bucket CORS is not supported",
                },
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "PutBucketCors",
        )


class LifecycleUnsupportedClient(FakeS3Client):
    def put_bucket_lifecycle_configuration(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": "InvalidArgument",
                    "Message": "Lifecycle schema is not supported",
                },
                "ResponseMetadata": {"HTTPStatusCode": 400},
            },
            "PutBucketLifecycleConfiguration",
        )


def test_safe_extension_discards_untrusted_suffixes() -> None:
    assert safe_extension("roads.GPKG") == ".gpkg"
    assert safe_extension("../../roads.shp") == ".shp"
    assert safe_extension("payload.really-long-extension") == ""
    assert safe_extension("no-extension") == ""


def test_ensure_bucket_creates_and_configures_storage(monkeypatch, settings) -> None:
    client = FakeS3Client()
    settings.S3_BUCKET = "geoportalx-test"
    settings.S3_REGION = "us-east-1"
    settings.S3_CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
    settings.S3_ABORT_INCOMPLETE_DAYS = 2
    monkeypatch.setattr(services, "get_s3_client", lambda: client)

    services.ensure_bucket()

    assert client.created == [{"Bucket": "geoportalx-test"}]
    assert client.cors[0]["Bucket"] == "geoportalx-test"
    assert client.lifecycle[0]["LifecycleConfiguration"]["Rules"][0][
        "AbortIncompleteMultipartUpload"
    ] == {"DaysAfterInitiation": 2}


def test_unsupported_bucket_cors_does_not_block_bootstrap(monkeypatch, settings) -> None:
    client = CorsUnsupportedClient()
    settings.S3_BUCKET = "geoportalx-test"
    settings.S3_REGION = "us-east-1"
    settings.S3_CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
    settings.S3_ABORT_INCOMPLETE_DAYS = 2
    monkeypatch.setattr(services, "get_s3_client", lambda: client)

    services.ensure_bucket()

    assert client.created == [{"Bucket": "geoportalx-test"}]
    assert len(client.lifecycle) == 1


def test_custom_endpoint_can_use_provider_managed_multipart_cleanup(
    monkeypatch,
    settings,
) -> None:
    client = LifecycleUnsupportedClient()
    settings.S3_BUCKET = "geoportalx-test"
    settings.S3_REGION = "us-east-1"
    settings.S3_ENDPOINT_URL = "http://minio:9000"
    settings.S3_CORS_ALLOWED_ORIGINS = []
    settings.S3_ABORT_INCOMPLETE_DAYS = 2
    monkeypatch.setattr(services, "get_s3_client", lambda: client)

    services.ensure_bucket()

    assert client.created == [{"Bucket": "geoportalx-test"}]
