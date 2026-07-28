from typing import Any
from uuid import UUID

from django.http import JsonResponse
from ninja import Router, Schema
from ninja.errors import HttpError

from modules.datasets.models import VectorLayer
from modules.vector_tiles.selectors import vector_layer_accessible_to

from .services import (
    VectorStylePermissionError,
    VectorStyleValidationError,
    update_vector_style,
    vector_style_payload,
)

router = Router(tags=["vector-styles"])


class VectorStyleUpdateSchema(Schema):
    mode: str = "SIMPLE"
    field_name: str | None = None
    classification_method: str | None = None
    class_count: int = 5
    palette: str = "BLUES"
    symbol: dict[str, Any] | None = None


@router.get("/{layer_id}/style")
def get_vector_style(request, layer_id: UUID):
    layer = _get_layer(request, layer_id)
    return JsonResponse(vector_style_payload(layer=layer, actor=request.user))


@router.put("/{layer_id}/style")
def put_vector_style(request, layer_id: UUID, payload: VectorStyleUpdateSchema):
    if not request.user.is_authenticated:
        raise HttpError(404, "Vector layer not found")
    layer = _get_layer(request, layer_id)
    try:
        update_vector_style(
            actor=request.user,
            layer=layer,
            mode=payload.mode,
            field_name=payload.field_name,
            classification_method=payload.classification_method,
            class_count=payload.class_count,
            palette=payload.palette,
            symbol=payload.symbol,
        )
    except VectorStylePermissionError as exc:
        raise HttpError(404, "Vector layer not found") from exc
    except VectorStyleValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    layer.refresh_from_db()
    return JsonResponse(vector_style_payload(layer=layer, actor=request.user))


def _get_layer(request, layer_id: UUID) -> VectorLayer:
    layer = vector_layer_accessible_to(request.user, layer_id)
    if layer is None:
        raise HttpError(404, "Vector layer not found")
    return layer
