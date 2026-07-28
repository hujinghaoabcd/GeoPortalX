from django.db.models import Q, QuerySet

from modules.accounts.models import User

from .models import Organization, OrganizationMember, OrganizationRole

_WRITE_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.DATA_MANAGER,
    OrganizationRole.PUBLISHER,
    OrganizationRole.EDITOR,
}


def organizations_for_user(user: User) -> QuerySet[Organization]:
    return (
        Organization.objects.select_related("owner")
        .filter(
            Q(owner=user) | Q(memberships__user=user, memberships__is_active=True),
            is_active=True,
        )
        .distinct()
    )


def organization_for_user(user: User, slug: str) -> Organization | None:
    return organizations_for_user(user).filter(slug=slug).first()


def can_create_organization_resource(user: User, organization: Organization) -> bool:
    if user.is_superuser or organization.owner_id == user.id:
        return True
    return OrganizationMember.objects.filter(
        organization=organization,
        user=user,
        is_active=True,
        role__in=_WRITE_ROLES,
    ).exists()
