from collections.abc import Iterable
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q
from django.utils import timezone

from modules.accounts.models import User
from modules.organizations.models import (
    Organization,
    OrganizationGroupMember,
)
from modules.resources.models import Resource, Visibility

from .models import PermissionAction, PermissionSubjectType, ResourcePermission

_ACTION_IMPLICATIONS: dict[str, set[str]] = {
    PermissionAction.VIEW: {PermissionAction.VIEW},
    PermissionAction.DOWNLOAD: {
        PermissionAction.DOWNLOAD,
        PermissionAction.EDIT,
        PermissionAction.PUBLISH,
        PermissionAction.MANAGE,
        PermissionAction.OWNER,
    },
    PermissionAction.EDIT: {
        PermissionAction.EDIT,
        PermissionAction.PUBLISH,
        PermissionAction.MANAGE,
        PermissionAction.OWNER,
    },
    PermissionAction.PUBLISH: {
        PermissionAction.PUBLISH,
        PermissionAction.MANAGE,
        PermissionAction.OWNER,
    },
    PermissionAction.MANAGE: {
        PermissionAction.MANAGE,
        PermissionAction.OWNER,
    },
    PermissionAction.OWNER: {PermissionAction.OWNER},
}


def actions_granting(required_action: str) -> Iterable[str]:
    try:
        return _ACTION_IMPLICATIONS[required_action]
    except KeyError as exc:
        raise ValueError(f"Unknown permission action: {required_action}") from exc


def has_resource_permission(
    user: User | AnonymousUser,
    resource: Resource,
    required_action: str,
    *,
    share_link_id: UUID | None = None,
) -> bool:
    """Evaluate one resource permission through the platform's central policy."""

    allowed_actions = tuple(actions_granting(required_action))

    if user.is_authenticated:
        if user.is_superuser or resource.owner_id == user.id:
            return True
        if required_action == PermissionAction.VIEW:
            if resource.visibility == Visibility.AUTHENTICATED:
                return True
            if (
                resource.visibility == Visibility.ORGANIZATION
                and resource.organization_id is not None
                and _organization_ids_for(user).filter(id=resource.organization_id).exists()
            ):
                return True
    elif required_action == PermissionAction.VIEW and resource.visibility == Visibility.PUBLIC:
        return True

    if required_action == PermissionAction.VIEW and resource.visibility == Visibility.PUBLIC:
        return True

    subject_filter = Q()
    if user.is_authenticated:
        subject_filter |= Q(
            subject_type=PermissionSubjectType.USER,
            subject_id=user.id,
        )
        subject_filter |= Q(
            subject_type=PermissionSubjectType.AUTHENTICATED,
            subject_id__isnull=True,
        )

        organization_ids = _organization_ids_for(user)
        subject_filter |= Q(
            subject_type=PermissionSubjectType.ORGANIZATION,
            subject_id__in=organization_ids,
        )

        group_ids = OrganizationGroupMember.objects.filter(
            user=user,
            is_active=True,
            group__organization__is_active=True,
            group__organization__memberships__user=user,
            group__organization__memberships__is_active=True,
        ).values_list("group_id", flat=True)
        subject_filter |= Q(
            subject_type=PermissionSubjectType.GROUP,
            subject_id__in=group_ids,
        )
    else:
        subject_filter |= Q(
            subject_type=PermissionSubjectType.ANONYMOUS,
            subject_id__isnull=True,
        )

    if share_link_id is not None:
        subject_filter |= Q(
            subject_type=PermissionSubjectType.SHARE_LINK,
            subject_id=share_link_id,
        )

    if not subject_filter:
        return False

    active_time = Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    return ResourcePermission.objects.filter(
        resource=resource,
        action__in=allowed_actions,
    ).filter(active_time).filter(subject_filter).exists()


def _organization_ids_for(user: User):
    return Organization.objects.filter(
        Q(owner=user) | Q(memberships__user=user, memberships__is_active=True),
        is_active=True,
    ).values_list("id", flat=True).distinct()
