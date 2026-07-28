import json
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection, transaction

from modules.datasets.models import VectorLayer


class FeatureQueryValidationError(ValueError):
    """Raised when a feature-query parameter is invalid."""


class FeatureQueryUnavailable(RuntimeError):
    """Raised when a layer cannot safely support interactive queries."""


class FeatureNotFound(LookupError):
    """Raised when a requested feature identifier does not exist."""


@dataclass(frozen=True, slots=True)
class FeaturePage:
    features: list[dict[str, Any]]
    next_cursor: int | None
    limit: int
    selected_fields: list[str]


@dataclass(frozen=True, slots=True)
class IdentifyResult:
    features: list[dict[str, Any]]
    selected_fields: list[str]
    tolerance_m: float


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None or not value.strip():
        return None
    pieces = [piece.strip() for piece in value.split(",")]
    if len(pieces) != 4:
        raise FeatureQueryValidationError(
            "bbox must contain min_longitude,min_latitude,max_longitude,max_latitude"
        )
    try:
        min_longitude, min_latitude, max_longitude, max_latitude = (
            float(piece) for piece in pieces
        )
    except ValueError as exc:
        raise FeatureQueryValidationError("bbox coordinates must be numeric") from exc

    _validate_longitude(min_longitude)
    _validate_longitude(max_longitude)
    _validate_latitude(min_latitude)
    _validate_latitude(max_latitude)
    if min_longitude >= max_longitude or min_latitude >= max_latitude:
        raise FeatureQueryValidationError("bbox minimums must be smaller than maximums")
    return min_longitude, min_latitude, max_longitude, max_latitude


def list_layer_features(
    *,
    layer: VectorLayer,
    limit: int,
    cursor: int | None,
    bbox: tuple[float, float, float, float] | None,
    requested_fields: str | None,
    include_geometry: bool,
) -> FeaturePage:
    _validate_layer(layer)
    bounded_limit = _bounded_limit(limit, maximum=int(settings.VECTOR_FEATURE_MAX_LIMIT))
    if cursor is not None and cursor < 0:
        raise FeatureQueryValidationError("cursor must be zero or greater")
    cursor_value = -1 if cursor is None else cursor
    selected_fields = _selected_fields(layer, requested_fields)

    fid = _quote("gx_fid")
    geometry = _quote(layer.geometry_column)
    table = _qualified_table(layer)
    select_sql = _select_columns(
        geometry=geometry,
        fields=selected_fields,
        include_geometry=include_geometry,
    )
    where_parts = [f"t.{fid} > %s"]
    parameters: list[Any] = [cursor_value]
    if bbox is not None:
        where_parts.append(
            f"t.{geometry} && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
        )
        where_parts.append(
            f"ST_Intersects(t.{geometry}, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        )
        parameters.extend(bbox)
        parameters.extend(bbox)
    parameters.append(bounded_limit + 1)
    query = (
        f"SELECT {select_sql} FROM {table} AS t "
        f"WHERE {' AND '.join(where_parts)} "
        f"ORDER BY t.{fid} ASC LIMIT %s"
    )
    rows = _fetchall(query, parameters)
    has_more = len(rows) > bounded_limit
    visible_rows = rows[:bounded_limit]
    features = [
        _feature_from_row(row, selected_fields=selected_fields)
        for row in visible_rows
    ]
    next_cursor = int(visible_rows[-1][0]) if has_more and visible_rows else None
    return FeaturePage(
        features=features,
        next_cursor=next_cursor,
        limit=bounded_limit,
        selected_fields=selected_fields,
    )


def get_layer_feature(
    *,
    layer: VectorLayer,
    feature_id: int,
    requested_fields: str | None,
    include_geometry: bool,
) -> dict[str, Any]:
    _validate_layer(layer)
    if feature_id < 0:
        raise FeatureQueryValidationError("feature_id must be zero or greater")
    selected_fields = _selected_fields(layer, requested_fields)
    fid = _quote("gx_fid")
    geometry = _quote(layer.geometry_column)
    table = _qualified_table(layer)
    select_sql = _select_columns(
        geometry=geometry,
        fields=selected_fields,
        include_geometry=include_geometry,
    )
    rows = _fetchall(
        f"SELECT {select_sql} FROM {table} AS t WHERE t.{fid} = %s LIMIT 1",
        [feature_id],
    )
    if not rows:
        raise FeatureNotFound(f"Feature {feature_id} was not found")
    return _feature_from_row(rows[0], selected_fields=selected_fields)


def identify_layer_features(
    *,
    layer: VectorLayer,
    longitude: float,
    latitude: float,
    tolerance_m: float,
    limit: int,
    requested_fields: str | None,
) -> IdentifyResult:
    _validate_layer(layer)
    _validate_longitude(longitude)
    _validate_latitude(latitude)
    maximum_tolerance = float(settings.VECTOR_IDENTIFY_MAX_TOLERANCE_METERS)
    if tolerance_m <= 0 or tolerance_m > maximum_tolerance:
        raise FeatureQueryValidationError(
            f"tolerance_m must be greater than zero and at most {maximum_tolerance:g}"
        )
    bounded_limit = _bounded_limit(
        limit,
        maximum=int(settings.VECTOR_IDENTIFY_MAX_LIMIT),
    )
    selected_fields = _selected_fields(layer, requested_fields)
    fid = _quote("gx_fid")
    geometry = _quote(layer.geometry_column)
    table = _qualified_table(layer)
    select_sql = _select_columns(
        geometry=geometry,
        fields=selected_fields,
        include_geometry=True,
    )
    point = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
    query = (
        f"SELECT {select_sql}, "
        f"ST_Distance(t.{geometry}::geography, {point}) AS distance_m "
        f"FROM {table} AS t "
        f"WHERE t.{geometry} IS NOT NULL "
        f"AND ST_DWithin(t.{geometry}::geography, {point}, %s) "
        f"ORDER BY distance_m ASC, t.{fid} ASC LIMIT %s"
    )
    parameters = [
        longitude,
        latitude,
        longitude,
        latitude,
        tolerance_m,
        bounded_limit,
    ]
    rows = _fetchall(query, parameters)
    features = [
        _feature_from_row(
            row,
            selected_fields=selected_fields,
            distance_m=float(row[-1]),
        )
        for row in rows
    ]
    return IdentifyResult(
        features=features,
        selected_fields=selected_fields,
        tolerance_m=tolerance_m,
    )


def _validate_layer(layer: VectorLayer) -> None:
    if not layer.db_schema or not layer.db_table or not layer.geometry_column:
        raise FeatureQueryUnavailable("Vector layer storage is not available")
    if layer.srid != 4326:
        raise FeatureQueryUnavailable(
            "Interactive feature queries require a published EPSG:4326 layer"
        )


def _bounded_limit(value: int, *, maximum: int) -> int:
    if value <= 0 or value > maximum:
        raise FeatureQueryValidationError(
            f"limit must be greater than zero and at most {maximum}"
        )
    return value


def _selected_fields(layer: VectorLayer, requested: str | None) -> list[str]:
    available = [
        name
        for field in layer.field_schema
        if (name := str(field.get("name", "")))
        and name not in {"gx_fid", layer.geometry_column}
    ]
    maximum = max(int(settings.VECTOR_FEATURE_MAX_FIELDS), 1)
    if requested is None or not requested.strip():
        return available[:maximum]

    selected: list[str] = []
    for value in requested.split(","):
        name = value.strip()
        if name and name not in selected:
            selected.append(name)
    if not selected:
        return []
    if len(selected) > maximum:
        raise FeatureQueryValidationError(f"At most {maximum} fields may be requested")
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise FeatureQueryValidationError(
            f"Unknown or unavailable fields: {', '.join(unknown)}"
        )
    return selected


def _select_columns(
    *,
    geometry: str,
    fields: list[str],
    include_geometry: bool,
) -> str:
    geometry_sql = (
        f"ST_AsGeoJSON(t.{geometry}, 6, 0)"
        if include_geometry
        else "NULL::text"
    )
    columns = [f"t.{_quote('gx_fid')}", geometry_sql]
    columns.extend(f"t.{_quote(field)}" for field in fields)
    return ", ".join(columns)


def _feature_from_row(
    row: tuple[Any, ...],
    *,
    selected_fields: list[str],
    distance_m: float | None = None,
) -> dict[str, Any]:
    geometry = json.loads(row[1]) if row[1] is not None else None
    properties = {
        name: value
        for name, value in zip(selected_fields, row[2 : 2 + len(selected_fields)], strict=True)
    }
    feature: dict[str, Any] = {
        "type": "Feature",
        "id": int(row[0]),
        "geometry": geometry,
        "properties": properties,
    }
    if distance_m is not None:
        feature["distance_m"] = distance_m
    return feature


def _fetchall(query: str, parameters: list[Any]) -> list[tuple[Any, ...]]:
    timeout_ms = max(int(settings.VECTOR_FEATURE_QUERY_TIMEOUT_MS), 1)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = %s", [timeout_ms])
        cursor.execute(query, parameters)
        return list(cursor.fetchall())


def _qualified_table(layer: VectorLayer) -> str:
    return f"{_quote(layer.db_schema)}.{_quote(layer.db_table)}"


def _quote(value: str) -> str:
    return connection.ops.quote_name(value)


def _validate_longitude(value: float) -> None:
    if value < -180 or value > 180:
        raise FeatureQueryValidationError("longitude must be between -180 and 180")


def _validate_latitude(value: float) -> None:
    if value < -90 or value > 90:
        raise FeatureQueryValidationError("latitude must be between -90 and 90")
