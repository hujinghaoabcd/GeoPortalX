import json
import math
import re
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAP_DOCUMENT_SCHEMA_VERSION = 1
MAP_DOCUMENT_MAX_LAYERS = 200
MAP_DOCUMENT_MAX_BYTES = 1024 * 1024
_LAYER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _validate_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("Map document JSON nesting exceeds the supported depth")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 10**15:
            raise ValueError("Map document integers exceed the supported range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Map document numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("Map document strings must not exceed 4096 characters")
        return value
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError("Map document arrays must not exceed 256 items")
        return [_validate_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("Map document objects must not exceed 64 keys")
        validated: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError("Map document object keys must be 1 to 128 characters")
            validated[key] = _validate_json_value(item, depth=depth + 1)
        return validated
    raise ValueError("Map document values must be JSON-compatible")


class MapViewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    center: tuple[float, float] = (0.0, 0.0)
    zoom: float = Field(default=2.0, ge=0.0, le=24.0)
    bearing: float = Field(default=0.0, ge=-180.0, le=180.0)
    pitch: float = Field(default=0.0, ge=0.0, le=85.0)

    @field_validator("center")
    @classmethod
    def validate_center(cls, center: tuple[float, float]) -> tuple[float, float]:
        longitude, latitude = center
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("Map center longitude must be between -180 and 180")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("Map center latitude must be between -90 and 90")
        return center


class MapLayerDocument(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    kind: Literal["VECTOR", "RASTER"]
    dataset_id: UUID
    binding: Literal["CURRENT", "PINNED"] = "CURRENT"
    dataset_version_id: UUID | None = None
    source_layer_name: str | None = Field(default=None, max_length=255)
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    min_zoom: float | None = Field(default=None, ge=0.0, le=24.0)
    max_zoom: float | None = Field(default=None, ge=0.0, le=24.0)
    style: dict[str, Any] = Field(default_factory=dict)
    filter: Any = None
    popup: dict[str, Any] = Field(default_factory=dict)
    legend: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_layer_id(cls, value: str) -> str:
        if not _LAYER_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "Layer id must start with an alphanumeric character and contain only "
                "letters, numbers, underscores, or hyphens"
            )
        return value

    @field_validator("style", "popup", "legend")
    @classmethod
    def validate_json_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_json_value(value)
        if not isinstance(validated, dict):
            raise ValueError("Expected a JSON object")
        return validated

    @field_validator("filter")
    @classmethod
    def validate_filter(cls, value: Any) -> Any:
        return _validate_json_value(value)

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if self.binding == "CURRENT" and self.dataset_version_id is not None:
            raise ValueError("CURRENT layer bindings cannot include dataset_version_id")
        if self.binding == "PINNED" and self.dataset_version_id is None:
            raise ValueError("PINNED layer bindings require dataset_version_id")
        if self.kind == "VECTOR" and not self.source_layer_name:
            raise ValueError("Vector layer references require source_layer_name")
        if self.kind == "RASTER" and self.source_layer_name:
            raise ValueError("Raster layer references cannot include source_layer_name")
        if (
            self.min_zoom is not None
            and self.max_zoom is not None
            and self.min_zoom > self.max_zoom
        ):
            raise ValueError("Layer min_zoom must not exceed max_zoom")
        return self


class MapDocumentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = MAP_DOCUMENT_SCHEMA_VERSION
    view: MapViewDocument = Field(default_factory=MapViewDocument)
    layers: list[MapLayerDocument] = Field(
        default_factory=list,
        max_length=MAP_DOCUMENT_MAX_LAYERS,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_json_value(value)
        if not isinstance(validated, dict):
            raise ValueError("Map metadata must be a JSON object")
        return validated

    @model_validator(mode="after")
    def validate_unique_layer_ids(self) -> Self:
        layer_ids = [layer.id for layer in self.layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("Map layer ids must be unique within a document version")
        return self


def validate_map_document(document: dict[str, Any]) -> tuple[MapDocumentSchema, dict[str, Any]]:
    parsed = MapDocumentSchema.model_validate(document)
    canonical = parsed.model_dump(mode="json", exclude_none=False)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAP_DOCUMENT_MAX_BYTES:
        raise ValueError(
            f"Map document exceeds the {MAP_DOCUMENT_MAX_BYTES}-byte serialized limit"
        )
    return parsed, canonical
