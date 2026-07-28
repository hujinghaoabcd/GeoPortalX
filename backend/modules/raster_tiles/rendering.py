import math
from typing import Any

from django.db import transaction

from modules.accounts.models import User
from modules.datasets.models import (
    RasterPublication,
    RasterRenderMode,
    RasterRenderSettings,
)
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission


class RasterRenderValidationError(ValueError):
    """Raised when raster render settings are outside the safe contract."""


class RasterRenderPermissionError(PermissionError):
    """Raised when an actor cannot update raster render settings."""


_ALLOWED_COLORMAPS = ("viridis", "plasma", "inferno", "magma", "terrain", "gray")
_ALLOWED_RESAMPLING = ("nearest", "bilinear", "cubic")


def render_settings_payload(publication: RasterPublication, *, actor) -> dict[str, Any]:
    render = publication.render_settings
    resource = publication.raster_dataset.dataset.resource
    return {
        "dataset_id": str(publication.raster_dataset_id),
        "publication_id": str(publication.id),
        "resource_id": str(resource.id),
        "resource_title": resource.title,
        "mode": render.mode,
        "bands": render.bands,
        "rescale": render.rescale,
        "colormap_name": render.colormap_name or None,
        "resampling": render.resampling,
        "opacity": render.opacity,
        "revision": render.revision,
        "available_bands": publication.bands,
        "statistics": publication.statistics,
        "allowed_colormaps": list(_ALLOWED_COLORMAPS),
        "allowed_resampling": list(_ALLOWED_RESAMPLING),
        "can_edit": bool(
            getattr(actor, "is_authenticated", False)
            and has_resource_permission(actor, resource, PermissionAction.EDIT)
        ),
    }


@transaction.atomic
def update_raster_render_settings(
    *,
    actor: User,
    publication: RasterPublication,
    mode: str,
    bands: list[int],
    rescale: list[list[float]],
    colormap_name: str | None,
    resampling: str,
    opacity: float,
) -> RasterRenderSettings:
    resource = publication.raster_dataset.dataset.resource
    if not has_resource_permission(actor, resource, PermissionAction.EDIT):
        raise RasterRenderPermissionError("User cannot edit raster rendering")
    normalized = validate_render_settings(
        publication=publication,
        mode=mode,
        bands=bands,
        rescale=rescale,
        colormap_name=colormap_name,
        resampling=resampling,
        opacity=opacity,
    )
    locked = RasterRenderSettings.objects.select_for_update().get(
        publication=publication,
    )
    locked.mode = normalized["mode"]
    locked.bands = normalized["bands"]
    locked.rescale = normalized["rescale"]
    locked.colormap_name = normalized["colormap_name"]
    locked.resampling = normalized["resampling"]
    locked.opacity = normalized["opacity"]
    locked.revision += 1
    locked.updated_by = actor
    locked.save()
    return locked


def validate_render_settings(
    *,
    publication: RasterPublication,
    mode: str,
    bands: list[int],
    rescale: list[list[float]],
    colormap_name: str | None,
    resampling: str,
    opacity: float,
) -> dict[str, Any]:
    if mode not in RasterRenderMode.values:
        raise RasterRenderValidationError("Unknown raster render mode")
    expected = 3 if mode == RasterRenderMode.RGB else 1
    normalized_bands = [int(value) for value in bands]
    if len(normalized_bands) != expected or len(set(normalized_bands)) != expected:
        raise RasterRenderValidationError(f"{mode} rendering requires {expected} unique bands")
    if any(value < 1 or value > publication.band_count for value in normalized_bands):
        raise RasterRenderValidationError("Raster band index is outside the dataset")
    if len(rescale) != expected:
        raise RasterRenderValidationError("Rescale range count must match selected bands")
    normalized_rescale: list[list[float]] = []
    for item in rescale:
        if len(item) != 2:
            raise RasterRenderValidationError("Each rescale entry must contain min and max")
        low, high = float(item[0]), float(item[1])
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            raise RasterRenderValidationError("Raster rescale values must be finite and ordered")
        normalized_rescale.append([low, high])
    normalized_colormap = str(colormap_name or "")
    if mode == RasterRenderMode.RGB:
        normalized_colormap = ""
    elif normalized_colormap not in _ALLOWED_COLORMAPS:
        raise RasterRenderValidationError("Unknown or unsupported raster colormap")
    if resampling not in _ALLOWED_RESAMPLING:
        raise RasterRenderValidationError("Unknown or unsupported raster resampling method")
    opacity_value = float(opacity)
    if not math.isfinite(opacity_value) or not 0.0 <= opacity_value <= 1.0:
        raise RasterRenderValidationError("Raster opacity must be between 0 and 1")
    return {
        "mode": mode,
        "bands": normalized_bands,
        "rescale": normalized_rescale,
        "colormap_name": normalized_colormap,
        "resampling": resampling,
        "opacity": opacity_value,
    }
