from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from modules.accounts.models import User
from modules.permissions.models import PermissionAction, ResourcePermission
from modules.permissions.selectors import organization_ids_for, permission_subject_filter_for
from modules.permissions.services import actions_granting

from .models import Resource, Visibility


def resources_accessible_to(
    user: User | AnonymousUser,
    required_action: str = PermissionAction.VIEW,
    *,
    share_link_id: UUID | None = None,
) -> QuerySet[Resource]:
    """Return resources available through one permission-aware SQL query."""

    allowed_actions = tuple(actions_granting(required_action))
    queryset = Resource.objects.select_related("owner", "organization")

    if user.is_authenticated and user.is_superuser:
        return queryset

    active_grants = ResourcePermission.objects.filter(
        resource_id=OuterRef("pk"),
        action__in=allowed_actions,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    active_grants = active_grants.filter(
        permission_subject_filter_for(user, share_link_id=share_link_id)
    )

    access_filter = Q(has_permission_grant=True)
    if user.is_authenticated:
        access_filter |= Q(owner_id=user.id)
        if required_action == PermissionAction.VIEW:
            access_filter |= Q(visibility__in=(Visibility.PUBLIC, Visibility.AUTHENTICATED))
            access_filter |= Q(
                visibility=Visibility.ORGANIZATION,
                organization_id__in=organization_ids_for(user),
            )
    elif required_action == PermissionAction.VIEW:
        access_filter |= Q(visibility=Visibility.PUBLIC)

    return queryset.annotate(has_permission_grant=Exists(active_grants)).filter(access_filter)


def resource_accessible_to(
    user: User | AnonymousUser,
    resource_id: UUID,
    required_action: str = PermissionAction.VIEW,
) -> Resource | None:
    return resources_accessible_to(user, required_action).filter(id=resource_id).first()
