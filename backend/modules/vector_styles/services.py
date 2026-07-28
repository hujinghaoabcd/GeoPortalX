import math
import re
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.db import transaction

from modules.accounts.models import User
from modules.datasets.models import VectorLayer
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission

from .models import (
    VectorStyle,
    VectorStyleClassificationMethod,
    VectorStyleMode,
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_CLASSES = 9

PALETTES: dict[str, tuple[str, ...]] = {
    "BLUES": (
        "#eff6ff",
        "#dbeafe",
        "#bfdbfe",
        "#93c5fd",
        "#60a5fa",
        "#3b82f6",
        "#2563eb",
        "#1d4ed8",
        "#1e3a8a",
    ),
    "VIRIDIS": (
        "#440154",
        "#482878",
        "#3e4989",
        "#31688e",
        "#26828e",
        "#1f9e89",
        "#35b779",
        "#6ece58",
        "#fde725",
    ),
    "SPECTRAL": (
        "#9e0142",
        "#d53e4f",
        "#f46d43",
        "#fdae61",
        "#ffffbf",
        "#abdda4",
        "#66c2a5",
        "#3288bd",
        "#5e4fa2",
    ),
    "ORANGE": (
        "#fff7ed",
        "#ffedd5",
        "#fed7aa",
        "#fdba74",
        "#fb923c",
        "#f97316",
        "#ea580c",
        "#c2410c",
        "#7c2d12",
    ),
    "CATEGORY10": (
        "#2563eb",
        "#dc2626",
        "#16a34a",
        "#9333ea",
        "#ea580c",
        "#0891b2",
        "#ca8a04",
        "#db2777",
        "#4f46e5",
    ),
}


class VectorStyleValidationError(ValueError):
    """Raised when a style request cannot be represented safely."""


class VectorStylePermissionError(PermissionError):
    """Raised when an actor cannot edit a vector style."""


def default_symbol_for_geometry(geometry_type: str) -> dict[str, Any]:
    normalized = str(geometry_type or "").upper()
    if "POINT" in normalized:
        return {
            "color": "#2563eb",
            "opacity": 0.85,
            "size": 6.0,
            "outline_color": "#ffffff",
            "outline_width": 1.0,
        }
    if "POLYGON" in normalized:
        return {
            "color": "#3b82f6",
            "opacity": 0.45,
            "outline_color": "#1d4ed8",
            "outline_width": 1.0,
        }
    return {"color": "#2563eb", "opacity": 0.9, "width": 2.5}


def ensure_vector_style(layer: VectorLayer) -> VectorStyle:
    style, _ = VectorStyle.objects.get_or_create(
        layer=layer,
        defaults={
            "symbol": default_symbol_for_geometry(layer.geometry_type),
            "fallback_symbol": {"color": "#9ca3af", "opacity": 0.65},
        },
    )
    if not style.symbol:
        style.symbol = default_symbol_for_geometry(layer.geometry_type)
        style.save(update_fields=("symbol", "updated_at"))
    return style


@transaction.atomic
def update_vector_style(
    *,
    actor: User,
    layer: VectorLayer,
    mode: str,
    field_name: str | None,
    classification_method: str | None,
    class_count: int,
    palette: str,
    symbol: dict[str, Any] | None,
) -> VectorStyle:
    resource = layer.vector_dataset.dataset.resource
    if not has_resource_permission(actor, resource, PermissionAction.EDIT):
        raise VectorStylePermissionError("Vector layer style is not editable")
    if mode not in VectorStyleMode.values:
        raise VectorStyleValidationError("Unknown vector style mode")
    palette_name = str(palette or "BLUES").upper()
    if palette_name not in PALETTES:
        raise VectorStyleValidationError("Unknown vector style palette")

    requested_count = int(class_count)
    if not 1 <= requested_count <= _MAX_CLASSES:
        raise VectorStyleValidationError(
            f"class_count must be between 1 and {_MAX_CLASSES}"
        )

    normalized_symbol = _normalize_symbol(layer.geometry_type, symbol or {})
    normalized_field = str(field_name or "").strip()
    normalized_method = str(classification_method or "").strip()
    classes: list[dict[str, Any]] = []

    if mode == VectorStyleMode.SIMPLE:
        normalized_field = ""
        normalized_method = ""
        requested_count = 1
    elif mode == VectorStyleMode.CATEGORICAL:
        if normalized_method not in {"", VectorStyleClassificationMethod.UNIQUE_VALUES}:
            raise VectorStyleValidationError(
                "Categorical styles require UNIQUE_VALUES classification"
            )
        normalized_method = VectorStyleClassificationMethod.UNIQUE_VALUES
        statistics = _field_statistics(layer, normalized_field)
        classes = _categorical_classes(
            statistics=statistics,
            class_count=requested_count,
            palette=palette_name,
        )
        requested_count = len(classes)
    else:
        if normalized_method not in {"", VectorStyleClassificationMethod.EQUAL_INTERVAL}:
            raise VectorStyleValidationError(
                "Graduated styles require EQUAL_INTERVAL classification"
            )
        normalized_method = VectorStyleClassificationMethod.EQUAL_INTERVAL
        if requested_count < 2:
            raise VectorStyleValidationError(
                "Graduated styles require at least two classes"
            )
        statistics = _field_statistics(layer, normalized_field)
        classes = _graduated_classes(
            statistics=statistics,
            class_count=requested_count,
            palette=palette_name,
        )

    style = VectorStyle.objects.select_for_update().filter(layer=layer).first()
    if style is None:
        style = VectorStyle(layer=layer)
    style.mode = mode
    style.field_name = normalized_field
    style.classification_method = normalized_method
    style.class_count = requested_count
    style.palette = palette_name
    style.symbol = normalized_symbol
    style.classes = classes
    style.fallback_symbol = {
        "color": "#9ca3af",
        "opacity": min(float(normalized_symbol.get("opacity", 0.65)), 0.75),
    }
    style.revision = (style.revision or 0) + 1
    style.updated_by = actor
    style.save()
    return style


def vector_style_payload(
    *,
    layer: VectorLayer,
    actor: User | AnonymousUser,
) -> dict[str, Any]:
    style = ensure_vector_style(layer)
    resource = layer.vector_dataset.dataset.resource
    can_edit = bool(
        actor.is_authenticated
        and has_resource_permission(actor, resource, PermissionAction.EDIT)
    )
    legend = _legend(style)
    return {
        "layer_id": str(layer.id),
        "dataset_id": str(layer.vector_dataset.dataset_id),
        "resource_id": str(resource.id),
        "layer_title": layer.title,
        "resource_title": resource.title,
        "geometry_type": layer.geometry_type,
        "feature_count": layer.feature_count,
        "bounds": (
            [float(value) for value in layer.extent.extent]
            if layer.extent is not None
            else [-180.0, -85.05112878, 180.0, 85.05112878]
        ),
        "style": {
            "id": str(style.id),
            "mode": style.mode,
            "field_name": style.field_name or None,
            "classification_method": style.classification_method or None,
            "class_count": style.class_count,
            "palette": style.palette,
            "symbol": style.symbol,
            "classes": style.classes,
            "fallback_symbol": style.fallback_symbol,
            "revision": style.revision,
            "updated_at": style.updated_at,
        },
        "legend": legend,
        "maplibre_layers": _maplibre_templates(layer=layer, style=style),
        "fields": _style_fields(layer),
        "palettes": [
            {"name": name, "colors": list(colors)}
            for name, colors in PALETTES.items()
        ],
        "can_edit": can_edit,
    }


def _field_statistics(layer: VectorLayer, field_name: str) -> dict[str, Any]:
    if not field_name:
        raise VectorStyleValidationError("A classification field is required")
    available_fields = {
        str(field.get("name", ""))
        for field in layer.field_schema
        if str(field.get("name", ""))
        not in {"", "gx_fid", layer.geometry_column}
    }
    if field_name not in available_fields:
        raise VectorStyleValidationError("Unknown vector style field")
    for statistics in layer.field_statistics:
        if str(statistics.get("name", "")) == field_name:
            return dict(statistics)
    raise VectorStyleValidationError(
        "The selected field does not have a persisted statistics profile"
    )


def _categorical_classes(
    *,
    statistics: dict[str, Any],
    class_count: int,
    palette: str,
) -> list[dict[str, Any]]:
    top_values = list(statistics.get("top_values") or [])
    if not top_values:
        raise VectorStyleValidationError(
            "The selected field does not have bounded common-value statistics"
        )
    selected = top_values[:class_count]
    colors = _palette_colors(palette, len(selected))
    return [
        {
            "value": str(item.get("value", "")),
            "label": str(item.get("value", "")),
            "count": int(item.get("count", 0)),
            "color": colors[index],
        }
        for index, item in enumerate(selected)
    ]


def _graduated_classes(
    *,
    statistics: dict[str, Any],
    class_count: int,
    palette: str,
) -> list[dict[str, Any]]:
    try:
        minimum = float(statistics["minimum"])
        maximum = float(statistics["maximum"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VectorStyleValidationError(
            "The selected field does not have numeric minimum and maximum statistics"
        ) from exc
    if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum <= minimum:
        raise VectorStyleValidationError(
            "The selected numeric field does not have a usable value range"
        )
    interval = (maximum - minimum) / class_count
    colors = _palette_colors(palette, class_count)
    classes = []
    for index in range(class_count):
        lower = minimum + interval * index
        upper = maximum if index == class_count - 1 else minimum + interval * (index + 1)
        classes.append(
            {
                "min": lower,
                "max": upper,
                "label": f"{_format_number(lower)} – {_format_number(upper)}",
                "color": colors[index],
            }
        )
    return classes


def _normalize_symbol(
    geometry_type: str,
    requested: dict[str, Any],
) -> dict[str, Any]:
    defaults = default_symbol_for_geometry(geometry_type)
    normalized = dict(defaults)
    normalized["color"] = _color(requested.get("color"), defaults["color"])
    normalized["opacity"] = _bounded_number(
        requested.get("opacity"), defaults["opacity"], 0.0, 1.0
    )
    geometry = str(geometry_type or "").upper()
    if "POINT" in geometry:
        normalized["size"] = _bounded_number(
            requested.get("size"), defaults["size"], 1.0, 30.0
        )
        normalized["outline_color"] = _color(
            requested.get("outline_color"), defaults["outline_color"]
        )
        normalized["outline_width"] = _bounded_number(
            requested.get("outline_width"), defaults["outline_width"], 0.0, 8.0
        )
    elif "POLYGON" in geometry:
        normalized["outline_color"] = _color(
            requested.get("outline_color"), defaults["outline_color"]
        )
        normalized["outline_width"] = _bounded_number(
            requested.get("outline_width"), defaults["outline_width"], 0.0, 8.0
        )
    else:
        normalized["width"] = _bounded_number(
            requested.get("width"), defaults["width"], 0.5, 20.0
        )
    return normalized


def _color(value: Any, default: str) -> str:
    candidate = str(value or default)
    if not _HEX_COLOR.fullmatch(candidate):
        raise VectorStyleValidationError("Style colors must use #RRGGBB notation")
    return candidate.lower()


def _bounded_number(value: Any, default: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise VectorStyleValidationError("Style symbol values must be numeric") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise VectorStyleValidationError(
            f"Style symbol value must be between {minimum:g} and {maximum:g}"
        )
    return number


def _palette_colors(palette: str, count: int) -> list[str]:
    colors = PALETTES[palette]
    if count <= 0:
        return []
    if count == 1:
        return [colors[len(colors) // 2]]
    return [
        colors[round(index * (len(colors) - 1) / (count - 1))]
        for index in range(count)
    ]


def _style_fields(layer: VectorLayer) -> list[dict[str, Any]]:
    statistics_by_name = {
        str(item.get("name", "")): item for item in layer.field_statistics
    }
    fields = []
    for field in layer.field_schema:
        name = str(field.get("name", ""))
        if name in {"", "gx_fid", layer.geometry_column}:
            continue
        statistics = statistics_by_name.get(name, {})
        minimum = statistics.get("minimum")
        maximum = statistics.get("maximum")
        numeric = isinstance(minimum, (int, float)) and isinstance(maximum, (int, float))
        fields.append(
            {
                "name": name,
                "data_type": field.get("data_type") or field.get("database_type"),
                "distinct_count": statistics.get("distinct_count"),
                "minimum": minimum,
                "maximum": maximum,
                "supports_categorical": bool(statistics.get("top_values")),
                "supports_graduated": numeric and maximum > minimum,
            }
        )
    return fields


def _color_expression(style: VectorStyle) -> Any:
    fallback = str(style.fallback_symbol.get("color") or "#9ca3af")
    if style.mode == VectorStyleMode.SIMPLE or not style.field_name:
        return str(style.symbol.get("color") or "#2563eb")
    if style.mode == VectorStyleMode.CATEGORICAL:
        expression: list[Any] = [
            "match",
            ["to-string", ["coalesce", ["get", style.field_name], ""]],
        ]
        for item in style.classes:
            expression.extend((str(item["value"]), str(item["color"])))
        expression.append(fallback)
        return expression
    first_color = str(style.classes[0]["color"])
    expression = [
        "step",
        ["to-number", ["get", style.field_name], float(style.classes[0]["min"])],
        first_color,
    ]
    for item in style.classes[1:]:
        expression.extend((float(item["min"]), str(item["color"])))
    return expression


def _maplibre_templates(
    *,
    layer: VectorLayer,
    style: VectorStyle,
) -> list[dict[str, Any]]:
    color = _color_expression(style)
    opacity = float(style.symbol.get("opacity", 0.8))
    geometry = str(layer.geometry_type or "").upper()
    if "POINT" in geometry:
        return [
            {
                "type": "circle",
                "paint": {
                    "circle-color": color,
                    "circle-opacity": opacity,
                    "circle-radius": float(style.symbol.get("size", 6.0)),
                    "circle-stroke-color": str(
                        style.symbol.get("outline_color", "#ffffff")
                    ),
                    "circle-stroke-width": float(
                        style.symbol.get("outline_width", 1.0)
                    ),
                },
            }
        ]
    if "POLYGON" in geometry:
        return [
            {
                "type": "fill",
                "paint": {"fill-color": color, "fill-opacity": opacity},
            },
            {
                "type": "line",
                "paint": {
                    "line-color": str(style.symbol.get("outline_color", "#1d4ed8")),
                    "line-width": float(style.symbol.get("outline_width", 1.0)),
                },
            },
        ]
    return [
        {
            "type": "line",
            "paint": {
                "line-color": color,
                "line-opacity": opacity,
                "line-width": float(style.symbol.get("width", 2.5)),
            },
        }
    ]


def _legend(style: VectorStyle) -> list[dict[str, Any]]:
    if style.mode == VectorStyleMode.SIMPLE:
        return [
            {
                "label": "全部要素",
                "color": str(style.symbol.get("color") or "#2563eb"),
            }
        ]
    legend = [
        {"label": str(item["label"]), "color": str(item["color"])}
        for item in style.classes
    ]
    legend.append(
        {
            "label": "其他或空值",
            "color": str(style.fallback_symbol.get("color") or "#9ca3af"),
        }
    )
    return legend


def _format_number(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")
