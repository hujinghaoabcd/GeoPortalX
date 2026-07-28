from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers
from ninja import Router
from ninja.errors import HttpError

from modules.datasets.models import VectorLayer
from modules.resources.models import Visibility

from .queries import (
    FeatureNotFound,
    FeatureQueryUnavailable,
    FeatureQueryValidationError,
    get_layer_feature,
    identify_layer_features,
    list_layer_features,
    parse_bbox,
)
from .selectors import vector_layer_accessible_to
from .services import (
    MartinSourceNotReady,
    MartinTileTooLarge,
    MartinUpstreamError,
    fetch_martin_tile,
)

router = Router(tags=["vector-tiles"])


@router.get("/{layer_id}/tilejson")
def get_tilejson(request, layer_id: UUID):
    layer = _get_layer(request, layer_id)
    response = JsonResponse(_tilejson_payload(request, layer))
    _apply_cache_policy(response, layer)
    return response


@router.get("/{layer_id}/source")
def get_maplibre_source(request, layer_id: UUID):
    layer = _get_layer(request, layer_id)
    tile_url = _tile_url(request, layer)
    payload = {
        "source": {
            "type": "vector",
            "tiles": [tile_url],
            "minzoom": layer.min_zoom,
            "maxzoom": layer.max_zoom,
            "bounds": _bounds(layer),
        },
        "source_layer": layer.tile_source_id,
        "geometry_type": layer.geometry_type,
        "bounds": _bounds(layer),
        "resource_id": str(layer.vector_dataset.dataset.resource_id),
        "dataset_id": str(layer.vector_dataset.dataset_id),
        "layer_id": str(layer.id),
    }
    response = JsonResponse(payload)
    _apply_cache_policy(response, layer)
    return response


@router.get("/{layer_id}/quality")
def get_quality_report(request, layer_id: UUID):
    layer = _get_layer(request, layer_id)
    response = JsonResponse(
        {
            "layer_id": str(layer.id),
            "feature_count": layer.feature_count,
            "quality_report": layer.quality_report,
            "field_statistics": layer.field_statistics,
        }
    )
    _apply_cache_policy(response, layer)
    return response


@router.get("/{layer_id}/features")
def list_features(
    request,
    layer_id: UUID,
    limit: int = 100,
    cursor: int | None = None,
    bbox: str | None = None,
    fields: str | None = None,
    include_geometry: bool = True,
):
    layer = _get_layer(request, layer_id)
    try:
        page = list_layer_features(
            layer=layer,
            limit=limit,
            cursor=cursor,
            bbox=parse_bbox(bbox),
            requested_fields=fields,
            include_geometry=include_geometry,
        )
    except FeatureQueryValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    except FeatureQueryUnavailable as exc:
        raise HttpError(409, str(exc)) from exc

    return _feature_response(
        {
            "type": "FeatureCollection",
            "features": page.features,
            "next_cursor": page.next_cursor,
            "limit": page.limit,
            "selected_fields": page.selected_fields,
            "layer_id": str(layer.id),
            "dataset_version_id": str(layer.version_id),
        },
        layer,
    )


@router.get("/{layer_id}/features/{feature_id}")
def get_feature(
    request,
    layer_id: UUID,
    feature_id: int,
    fields: str | None = None,
    include_geometry: bool = True,
):
    layer = _get_layer(request, layer_id)
    try:
        feature = get_layer_feature(
            layer=layer,
            feature_id=feature_id,
            requested_fields=fields,
            include_geometry=include_geometry,
        )
    except FeatureQueryValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    except FeatureQueryUnavailable as exc:
        raise HttpError(409, str(exc)) from exc
    except FeatureNotFound as exc:
        raise HttpError(404, str(exc)) from exc

    feature["geoportalx"] = {
        "layer_id": str(layer.id),
        "dataset_version_id": str(layer.version_id),
    }
    return _feature_response(feature, layer)


@router.get("/{layer_id}/identify")
def identify_features(
    request,
    layer_id: UUID,
    longitude: float,
    latitude: float,
    tolerance_m: float = 25.0,
    limit: int = 5,
    fields: str | None = None,
):
    layer = _get_layer(request, layer_id)
    try:
        result = identify_layer_features(
            layer=layer,
            longitude=longitude,
            latitude=latitude,
            tolerance_m=tolerance_m,
            limit=limit,
            requested_fields=fields,
        )
    except FeatureQueryValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    except FeatureQueryUnavailable as exc:
        raise HttpError(409, str(exc)) from exc

    return _feature_response(
        {
            "type": "FeatureCollection",
            "features": result.features,
            "selected_fields": result.selected_fields,
            "tolerance_m": result.tolerance_m,
            "query_point": [longitude, latitude],
            "layer_id": str(layer.id),
            "dataset_version_id": str(layer.version_id),
        },
        layer,
    )


@router.get("/{layer_id}/tiles/{z}/{x}/{y}")
def get_tile(request, layer_id: UUID, z: int, x: int, y: int):
    layer = _get_layer(request, layer_id)
    _validate_tile_coordinates(layer, z=z, x=x, y=y)
    try:
        tile = fetch_martin_tile(
            layer=layer,
            z=z,
            x=x,
            y=y,
            accept_encoding=request.headers.get("Accept-Encoding"),
            if_none_match=request.headers.get("If-None-Match"),
            if_modified_since=request.headers.get("If-Modified-Since"),
        )
    except MartinSourceNotReady as exc:
        response = JsonResponse(
            {"detail": str(exc)},
            status=503,
        )
        response["Retry-After"] = "5"
        response["Cache-Control"] = "no-store"
        return response
    except MartinTileTooLarge as exc:
        raise HttpError(502, str(exc)) from exc
    except MartinUpstreamError as exc:
        raise HttpError(502, str(exc)) from exc

    response = HttpResponse(
        tile.body,
        status=tile.status,
        content_type=tile.content_type,
    )
    if tile.content_encoding:
        response["Content-Encoding"] = tile.content_encoding
    if tile.etag:
        response["ETag"] = tile.etag
    if tile.last_modified:
        response["Last-Modified"] = tile.last_modified
    response["X-Content-Type-Options"] = "nosniff"
    _apply_cache_policy(response, layer)
    patch_vary_headers(response, ("Accept-Encoding",))
    return response


def _get_layer(request, layer_id: UUID) -> VectorLayer:
    layer = vector_layer_accessible_to(request.user, layer_id)
    if layer is None:
        raise HttpError(404, "Vector layer not found")
    return layer


def _tilejson_payload(request, layer: VectorLayer) -> dict[str, Any]:
    resource = layer.vector_dataset.dataset.resource
    fields = {
        str(field["name"]): str(
            field.get("database_type") or field.get("data_type") or "string"
        )
        for field in layer.field_schema
        if str(field.get("name", "")) not in {"", layer.geometry_column}
    }
    return {
        "tilejson": "3.0.0",
        "name": layer.title,
        "description": resource.description or resource.title,
        "tiles": [_tile_url(request, layer)],
        "minzoom": layer.min_zoom,
        "maxzoom": layer.max_zoom,
        "bounds": _bounds(layer),
        "vector_layers": [
            {
                "id": layer.tile_source_id,
                "fields": fields,
                "minzoom": layer.min_zoom,
                "maxzoom": layer.max_zoom,
            }
        ],
        "geoportalx": {
            "resource_id": str(resource.id),
            "dataset_id": str(layer.vector_dataset.dataset_id),
            "dataset_version_id": str(layer.version_id),
            "layer_id": str(layer.id),
            "geometry_type": layer.geometry_type,
            "feature_count": layer.feature_count,
        },
    }


def _tile_url(request, layer: VectorLayer) -> str:
    path = (
        f"/api/v1/vector-layers/{layer.id}/tiles/"
        f"{{z}}/{{x}}/{{y}}?version={layer.version_id}"
    )
    return f"{request.scheme}://{request.get_host()}{path}"


def _bounds(layer: VectorLayer) -> list[float]:
    if layer.extent is None:
        return [-180.0, -85.05112878, 180.0, 85.05112878]
    return [float(value) for value in layer.extent.extent]


def _validate_tile_coordinates(layer: VectorLayer, *, z: int, x: int, y: int) -> None:
    if z < layer.min_zoom or z > layer.max_zoom:
        raise HttpError(404, "Tile zoom is outside the published range")
    tile_count = 1 << z
    if x < 0 or y < 0 or x >= tile_count or y >= tile_count:
        raise HttpError(404, "Tile coordinates are outside the XYZ matrix")


def _feature_response(payload: dict[str, Any], layer: VectorLayer) -> JsonResponse:
    response = JsonResponse(payload)
    maximum = max(int(settings.VECTOR_FEATURE_MAX_RESPONSE_BYTES), 1)
    if len(response.content) > maximum:
        raise HttpError(
            413,
            f"Feature response exceeded the configured {maximum}-byte limit",
        )
    _apply_cache_policy(response, layer)
    return response


def _apply_cache_policy(response: HttpResponse, layer: VectorLayer) -> None:
    visibility = layer.vector_dataset.dataset.resource.visibility
    if visibility == Visibility.PUBLIC:
        seconds = max(int(settings.VECTOR_TILE_PUBLIC_CACHE_SECONDS), 0)
        response["Cache-Control"] = f"public, max-age={seconds}"
    else:
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Cookie", "Authorization"))
