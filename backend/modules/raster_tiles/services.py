import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from modules.datasets.models import RasterPublication, RasterRenderSettings


class TiTilerError(RuntimeError):
    """Base error for the internal TiTiler service."""


class TiTilerUpstreamError(TiTilerError):
    """Raised when TiTiler cannot serve a request."""


class TiTilerResponseTooLarge(TiTilerError):
    """Raised when an upstream response exceeds a configured safety limit."""


@dataclass(frozen=True, slots=True)
class TiTilerResponse:
    status: int
    body: bytes
    content_type: str
    etag: str | None
    last_modified: str | None


def fetch_raster_tile(
    *,
    publication: RasterPublication,
    render: RasterRenderSettings,
    z: int,
    x: int,
    y: int,
) -> TiTilerResponse:
    return _fetch(
        path=f"/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png",
        parameters=_render_parameters(publication, render),
        accept="image/png",
        maximum=int(getattr(settings, "TITILER_MAX_TILE_BYTES", 20 * 1024 * 1024)),
    )


def fetch_raster_point(
    *,
    publication: RasterPublication,
    longitude: float,
    latitude: float,
) -> dict[str, Any]:
    response = _fetch(
        path=f"/cog/point/{longitude},{latitude}",
        parameters={"url": _asset_url(publication)},
        accept="application/json",
        maximum=2 * 1024 * 1024,
    )
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TiTilerUpstreamError("TiTiler returned invalid point JSON") from exc
    if not isinstance(payload, dict):
        raise TiTilerUpstreamError("TiTiler returned an invalid point response")
    return payload


def _render_parameters(
    publication: RasterPublication,
    render: RasterRenderSettings,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "url": _asset_url(publication),
        "bidx": [int(value) for value in render.bands],
        "rescale": [f"{float(item[0])},{float(item[1])}" for item in render.rescale],
        "resampling": render.resampling,
        "return_mask": "true",
    }
    if render.colormap_name:
        parameters["colormap_name"] = render.colormap_name
    return parameters


def _asset_url(publication: RasterPublication) -> str:
    return f"s3://{publication.bucket}/{publication.object_key}"


def _fetch(
    *,
    path: str,
    parameters: dict[str, Any],
    accept: str,
    maximum: int,
) -> TiTilerResponse:
    base_url = str(
        getattr(settings, "TITILER_INTERNAL_URL", "http://localhost:8001")
    ).rstrip("/")
    url = f"{base_url}{path}?{urlencode(parameters, doseq=True)}"
    request = Request(url, headers={"Accept": accept}, method="GET")
    timeout = float(getattr(settings, "TITILER_REQUEST_TIMEOUT", 20.0))
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise TiTilerResponseTooLarge(
                    f"TiTiler response exceeded the configured {maximum}-byte limit"
                )
            return TiTilerResponse(
                status=int(response.status),
                body=body,
                content_type=response.headers.get("Content-Type", accept),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except HTTPError as exc:
        message = exc.read(2048).decode("utf-8", errors="replace").strip()
        raise TiTilerUpstreamError(
            f"TiTiler returned HTTP {exc.code}: {message or 'upstream error'}"
        ) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise TiTilerUpstreamError(f"TiTiler request failed: {exc}") from exc
