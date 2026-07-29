from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers
from ninja import Router, Schema
from ninja.errors import HttpError

from modules.datasets.models import Dataset, RasterPublication, RasterPublicationStatus
from modules.datasets.raster_services import request_raster_publication
from modules.resources.models import Visibility

from .rendering import (
    RasterRenderPermissionError,
    RasterRenderValidationError,
    render_settings_payload,
    update_raster_render_settings,
)
from .selectors import raster_publication_accessible_to
from .services import (
    TiTilerResponseTooLarge,
    TiTilerUpstreamError,
    fetch_raster_point,
    fetch_raster_tile,
)

router = Router(tags=["raster-publishing"])


class RasterRenderUpdateSchema(Schema):
    mode: str
    bands: list[int]
    rescale: list[list[float]]
    colormap_name: str | None = None
    resampling: str = "bilinear"
    opacity: float = 1.0


@router.post("/{dataset_id}/publish")
def publish_raster_dataset(request, dataset_id: UUID):
    if not request.user.is_authenticated:
        raise HttpError(404, "Raster dataset not found")
    try:
        result = request_raster_publication(actor=request.user, dataset_id=dataset_id)
    except (PermissionError, Dataset.DoesNotExist) as exc:
        raise HttpError(404, "Raster dataset not found") from exc
    except ValueError as exc:
        raise HttpError(409, str(exc)) from exc
    return JsonResponse(_publication_payload(result.publication), status=202)


@router.get("/{dataset_id}/publication")
def get_raster_publication(request, dataset_id: UUID):
    publication = raster_publication_accessible_to(
        request.user,
        dataset_id,
        ready_only=False,
    )
    if publication is None:
        raise HttpError(404, "Raster publication not found")
    return JsonResponse(_publication_payload(publication))


@router.get("/{dataset_id}/source")
def get_raster_source(request, dataset_id: UUID):
    publication = _get_ready_publication(request, dataset_id)
    render = publication.render_settings
    payload = {
        "source": {
            "type": "raster",
            "tiles": [_tile_url(request, publication, render.revision)],
            "tileSize": 256,
            "minzoom": publication.min_zoom,
            "maxzoom": publication.max_zoom,
            "bounds": publication.bounds,
        },
        "bounds": publication.bounds,
        "dataset_id": str(publication.raster_dataset_id),
        "dataset_version_id": str(publication.version_id),
        "publication_id": str(publication.id),
        "width": publication.width,
        "height": publication.height,
        "band_count": publication.band_count,
        "crs": publication.crs,
        "epsg": publication.epsg,
        "opacity": render.opacity,
        "revision": render.revision,
    }
    response = JsonResponse(payload)
    _apply_cache_policy(response, publication)
    return response


@router.get("/{dataset_id}/tilejson")
def get_raster_tilejson(request, dataset_id: UUID):
    publication = _get_ready_publication(request, dataset_id)
    render = publication.render_settings
    resource = publication.raster_dataset.dataset.resource
    payload = {
        "tilejson": "3.0.0",
        "name": resource.title,
        "description": resource.description or resource.title,
        "tiles": [_tile_url(request, publication, render.revision)],
        "minzoom": publication.min_zoom,
        "maxzoom": publication.max_zoom,
        "bounds": publication.bounds,
        "geoportalx": {
            "resource_id": str(resource.id),
            "dataset_id": str(publication.raster_dataset_id),
            "dataset_version_id": str(publication.version_id),
            "publication_id": str(publication.id),
            "band_count": publication.band_count,
            "render_revision": render.revision,
        },
    }
    response = JsonResponse(payload)
    _apply_cache_policy(response, publication)
    return response


@router.get("/{dataset_id}/rendering")
def get_raster_rendering(request, dataset_id: UUID):
    publication = _get_ready_publication(request, dataset_id)
    response = JsonResponse(render_settings_payload(publication, actor=request.user))
    _apply_cache_policy(response, publication)
    return response


@router.put("/{dataset_id}/rendering")
def put_raster_rendering(
    request,
    dataset_id: UUID,
    payload: RasterRenderUpdateSchema,
):
    if not request.user.is_authenticated:
        raise HttpError(404, "Raster dataset not found")
    publication = _get_ready_publication(request, dataset_id)
    try:
        update_raster_render_settings(
            actor=request.user,
            publication=publication,
            mode=payload.mode,
            bands=payload.bands,
            rescale=payload.rescale,
            colormap_name=payload.colormap_name,
            resampling=payload.resampling,
            opacity=payload.opacity,
        )
    except RasterRenderPermissionError as exc:
        raise HttpError(404, "Raster dataset not found") from exc
    except RasterRenderValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    publication.refresh_from_db()
    return JsonResponse(render_settings_payload(publication, actor=request.user))


@router.get("/{dataset_id}/tiles/{z}/{x}/{y}.png")
def get_raster_tile(request, dataset_id: UUID, z: int, x: int, y: int):
    publication = _get_ready_publication(request, dataset_id)
    _validate_tile_coordinates(publication, z=z, x=x, y=y)
    try:
        tile = fetch_raster_tile(
            publication=publication,
            render=publication.render_settings,
            z=z,
            x=x,
            y=y,
        )
    except TiTilerResponseTooLarge as exc:
        raise HttpError(502, str(exc)) from exc
    except TiTilerUpstreamError as exc:
        raise HttpError(502, str(exc)) from exc
    response = HttpResponse(tile.body, status=tile.status, content_type=tile.content_type)
    if tile.etag:
        response["ETag"] = tile.etag
    if tile.last_modified:
        response["Last-Modified"] = tile.last_modified
    response["X-Content-Type-Options"] = "nosniff"
    _apply_cache_policy(response, publication)
    return response


@router.get("/{dataset_id}/point")
def get_raster_point(
    request,
    dataset_id: UUID,
    longitude: float,
    latitude: float,
):
    publication = _get_ready_publication(request, dataset_id)
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise HttpError(400, "Point coordinates are outside WGS84 bounds")
    try:
        point = fetch_raster_point(
            publication=publication,
            longitude=longitude,
            latitude=latitude,
        )
    except TiTilerUpstreamError as exc:
        raise HttpError(502, str(exc)) from exc
    response = JsonResponse(
        {
            "dataset_id": str(publication.raster_dataset_id),
            "dataset_version_id": str(publication.version_id),
            "publication_id": str(publication.id),
            "query_point": [longitude, latitude],
            "result": point,
        }
    )
    _apply_cache_policy(response, publication)
    return response


def _get_ready_publication(request, dataset_id: UUID) -> RasterPublication:
    publication = raster_publication_accessible_to(request.user, dataset_id)
    if publication is None:
        raise HttpError(404, "Raster dataset not found")
    return publication


def _publication_payload(publication: RasterPublication) -> dict[str, Any]:
    job = publication.job
    effective_status = publication.status
    if (
        effective_status == RasterPublicationStatus.PENDING
        and job is not None
        and job.status in {"FAILED", "CANCELLED"}
    ):
        effective_status = job.status
    return {
        "id": str(publication.id),
        "dataset_id": str(publication.raster_dataset_id),
        "dataset_version_id": str(publication.version_id),
        "status": effective_status,
        "job_id": str(job.id) if job else None,
        "job_status": job.status if job else None,
        "job_progress": job.progress if job else None,
        "job_message": job.progress_message if job else None,
        "object_size": publication.object_size,
        "checksum_sha256": publication.checksum_sha256,
        "width": publication.width,
        "height": publication.height,
        "band_count": publication.band_count,
        "crs": publication.crs,
        "epsg": publication.epsg,
        "bounds": publication.bounds,
        "statistics": publication.statistics,
        "cog_profile": publication.cog_profile,
        "min_zoom": publication.min_zoom,
        "max_zoom": publication.max_zoom,
        "failure_code": publication.failure_code or (job.error_code if job else ""),
        "failure_message": publication.failure_message or (job.error_message if job else ""),
        "created_at": publication.created_at.isoformat(),
        "started_at": publication.started_at.isoformat() if publication.started_at else None,
        "completed_at": (
            publication.completed_at.isoformat() if publication.completed_at else None
        ),
    }


def _tile_url(request, publication: RasterPublication, revision: int) -> str:
    path = (
        f"/api/v1/raster-datasets/{publication.raster_dataset_id}/tiles/"
        f"{{z}}/{{x}}/{{y}}.png?revision={revision}"
    )
    return f"{request.scheme}://{request.get_host()}{path}"


def _validate_tile_coordinates(
    publication: RasterPublication,
    *,
    z: int,
    x: int,
    y: int,
) -> None:
    if z < publication.min_zoom or z > publication.max_zoom:
        raise HttpError(404, "Tile zoom is outside the published range")
    count = 1 << z
    if x < 0 or y < 0 or x >= count or y >= count:
        raise HttpError(404, "Tile coordinates are outside the XYZ matrix")


def _apply_cache_policy(response: HttpResponse, publication: RasterPublication) -> None:
    visibility = publication.raster_dataset.dataset.resource.visibility
    if visibility == Visibility.PUBLIC:
        seconds = max(int(getattr(settings, "RASTER_TILE_PUBLIC_CACHE_SECONDS", 60)), 0)
        response["Cache-Control"] = f"public, max-age={seconds}"
    else:
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Cookie", "Authorization"))
