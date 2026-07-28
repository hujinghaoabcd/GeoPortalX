from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q, QuerySet

from modules.accounts.models import User
from modules.organizations.models import Organization, OrganizationGroupMember

from .models import PermissionSubjectType


def organization_ids_for(user: User) -> QuerySet[UUID]:
    return (
        Organization.objects.filter(
            Q(owner=user) | Q(memberships__user=user, memberships__is_active=True),
            is_active=True,
        )
        .values_list("id", flat=True)
        .distinct()
    )


def group_ids_for(user: User) -> QuerySet[UUID]:
    return OrganizationGroupMember.objects.filter(
        user=user,
        is_active=True,
        group__organization__is_active=True,
        group__organization__memberships__user=user,
        group__organization__memberships__is_active=True,
    ).values_list("group_id", flat=True)


def permission_subject_filter_for(
    user: User | AnonymousUser,
    *,
    share_link_id: UUID | None,
) -> Q:
    subject_filter = Q()
    if user.is_authenticated:
        subject_filter |= Q(subject_type=PermissionSubjectType.USER, subject_id=user.id)
        subject_filter |= Q(
            subject_type=PermissionSubjectType.AUTHENTICATED,
            subject_id__isnull=True,
        )
        subject_filter |= Q(
            subject_type=PermissionSubjectType.ORGANIZATION,
            subject_id__in=organization_ids_for(user),
        )
        subject_filter |= Q(
            subject_type=PermissionSubjectType.GROUP,
            subject_id__in=group_ids_for(user),
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
    return subject_filter
