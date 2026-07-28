from functools import lru_cache

import boto3
from botocore.config import Config
from django.conf import settings


@lru_cache(maxsize=1)
def get_s3_client():
    """Return the process-wide S3-compatible client.

    Path-style addressing is the development default because it works with MinIO.
    Production AWS deployments can switch to virtual-hosted addressing by setting
    ``S3_ADDRESSING_STYLE=virtual``.
    """

    endpoint_url = settings.S3_ENDPOINT_URL or None
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        aws_session_token=settings.S3_SESSION_TOKEN or None,
        region_name=settings.S3_REGION,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 4, "mode": "standard"},
            s3={"addressing_style": settings.S3_ADDRESSING_STYLE},
        ),
    )


def clear_s3_client_cache() -> None:
    """Clear the cached client, primarily for tests and rotated credentials."""

    get_s3_client.cache_clear()
