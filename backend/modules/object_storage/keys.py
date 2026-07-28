import re
from pathlib import PurePath
from uuid import UUID

_ALLOWED_EXTENSION = re.compile(r"^\.[a-z0-9]{1,12}$")


def safe_extension(filename: str) -> str:
    """Keep only a conservative lowercase extension from an untrusted filename."""

    suffix = PurePath(filename.replace("\\", "/")).suffix.lower()
    return suffix if _ALLOWED_EXTENSION.fullmatch(suffix) else ""


def source_upload_key(*, owner_id: UUID, upload_id: UUID, filename: str) -> str:
    """Build a non-guessable key without embedding user-controlled path segments."""

    return f"uploads/{owner_id}/{upload_id}/source{safe_extension(filename)}"


def object_uri(*, bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"
