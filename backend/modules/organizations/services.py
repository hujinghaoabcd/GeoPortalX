from django.db import transaction

from modules.accounts.models import User

from .models import Organization, OrganizationMember, OrganizationRole


@transaction.atomic
def create_organization(
    *,
    owner: User,
    name: str,
    slug: str,
    description: str = "",
) -> Organization:
    """Create an organization and its canonical owner membership together."""

    organization = Organization.objects.create(
        owner=owner,
        name=name,
        slug=slug,
        description=description,
    )
    OrganizationMember.objects.create(
        organization=organization,
        user=owner,
        role=OrganizationRole.OWNER,
    )
    return organization


@transaction.atomic
def set_member_role(
    *,
    organization: Organization,
    user: User,
    role: str,
) -> OrganizationMember:
    """Create or reactivate one organization membership with a validated role."""

    if role not in OrganizationRole.values:
        raise ValueError(f"Unknown organization role: {role}")

    membership, _ = OrganizationMember.objects.update_or_create(
        organization=organization,
        user=user,
        defaults={"role": role, "is_active": True},
    )
    return membership


@transaction.atomic
def deactivate_member(
    *,
    organization: Organization,
    user: User,
) -> None:
    """Deactivate a member without deleting historical membership records."""

    if organization.owner_id == user.id:
        raise ValueError("The organization owner cannot be deactivated")

    OrganizationMember.objects.filter(
        organization=organization,
        user=user,
        is_active=True,
    ).update(is_active=False)
