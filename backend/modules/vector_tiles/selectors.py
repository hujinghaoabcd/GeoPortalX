from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet

from modules.accounts.models import User
from modules.permissions.models import PermissionAction
from modules.resources.selectors import resources_accessible_to

from modules.datasets.models import VectorLayer, VectorLayerStatus


def vector_layers_accessible_to(
    user: User | AnonymousUser,
    required_action: str = PermissionAction.VIEW,
) -> QuerySet[VectorLayer]:
    accessible_resources = resources_accessible_to(user, required_action).values("pk")
    return VectorLayer.objects.select_related(
        "version",
        "vector_dataset",
        "vector_dataset__dataset",
        "vector_dataset__dataset__resource",
    ).filter(
        status=VectorLayerStatus.READY,
        tile_source_id__gt="",
        vector_dataset__dataset__resource_id__in=accessible_resources,
    )


def vector_layer_accessible_to(
    user: User | AnonymousUser,
    layer_id: UUID,
    required_action: str = PermissionAction.VIEW,
) -> VectorLayer | None:
    return vector_layers_accessible_to(user, required_action).filter(pk=layer_id).first()
