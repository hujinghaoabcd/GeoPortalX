from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings

from modules.datasets.models import VectorLayer


class MartinError(RuntimeError):
    """Base error for the internal Martin tile service."""


class MartinSourceNotReady(MartinError):
    """Raised when Martin has not discovered a newly promoted table yet."""


class MartinUpstreamError(MartinError):
    """Raised when the internal Martin service cannot serve a request."""


class MartinTileTooLarge(MartinError):
    """Raised when an upstream tile exceeds the configured proxy safety limit."""


@dataclass(frozen=True, slots=True)
class MartinTile:
    status: int
    body: bytes
    content_type: str
    content_encoding: str | None
    etag: str | None
    last_modified: str | None


def fetch_martin_tile(
    *,
    layer: VectorLayer,
    z: int,
    x: int,
    y: int,
    accept_encoding: str | None = None,
    if_none_match: str | None = None,
    if_modified_since: str | None = None,
) -> MartinTile:
    source_id = quote(layer.tile_source_id, safe="")
    base_url = str(settings.MARTIN_INTERNAL_URL).rstrip("/")
    url = f"{base_url}/{source_id}/{z}/{x}/{y}"
    headers = {"Accept": "application/x-protobuf"}
    if accept_encoding:
        headers["Accept-Encoding"] = accept_encoding
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=float(settings.MARTIN_REQUEST_TIMEOUT)) as response:
            status = int(response.status)
            body = _read_bounded(response)
            return MartinTile(
                status=status,
                body=body,
                content_type=response.headers.get(
                    "Content-Type",
                    "application/vnd.mapbox-vector-tile",
                ),
                content_encoding=response.headers.get("Content-Encoding"),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except HTTPError as exc:
        if exc.code == 304:
            return MartinTile(
                status=304,
                body=b"",
                content_type=exc.headers.get(
                    "Content-Type",
                    "application/vnd.mapbox-vector-tile",
                ),
                content_encoding=exc.headers.get("Content-Encoding"),
                etag=exc.headers.get("ETag"),
                last_modified=exc.headers.get("Last-Modified"),
            )
        if exc.code == 404:
            raise MartinSourceNotReady(
                f"Martin has not published source {layer.tile_source_id}"
            ) from exc
        message = exc.read(1024).decode("utf-8", errors="replace").strip()
        raise MartinUpstreamError(
            f"Martin returned HTTP {exc.code}: {message or 'upstream error'}"
        ) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise MartinUpstreamError(f"Martin request failed: {exc}") from exc


def _read_bounded(response) -> bytes:
    maximum = max(int(settings.MARTIN_MAX_TILE_BYTES), 1)
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise MartinTileTooLarge(
            f"Martin tile exceeded the configured {maximum}-byte proxy limit"
        )
    return body
