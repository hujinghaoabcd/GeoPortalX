from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet

from modules.accounts.models import User
from modules.permissions.models import PermissionAction
from modules.resources.models import ResourceType
from modules.resources.selectors import resources_accessible_to

from .models import MapDocument


def map_documents_accessible_to(
    user: User | AnonymousUser,
    required_action: str = PermissionAction.VIEW,
) -> QuerySet[MapDocument]:
    resource_ids = resources_accessible_to(user, required_action).filter(
        resource_type=ResourceType.MAP,
    ).values("id")
    return (
        MapDocument.objects.filter(resource_id__in=resource_ids)
        .select_related("resource", "current_version")
        .order_by("-updated_at")
    )


def map_document_accessible_to(
    user: User | AnonymousUser,
    map_document_id: UUID,
    required_action: str = PermissionAction.VIEW,
) -> MapDocument | None:
    return (
        map_documents_accessible_to(user, required_action)
        .filter(pk=map_document_id)
        .first()
    )
