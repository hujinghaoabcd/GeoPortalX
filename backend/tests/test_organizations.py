import pytest

from modules.accounts.models import User
from modules.organizations.models import OrganizationRole
from modules.organizations.services import (
    create_organization,
    deactivate_member,
    set_member_role,
)


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


@pytest.mark.django_db
def test_create_organization_creates_owner_membership() -> None:
    owner = _user("organization-service-owner")

    organization = create_organization(
        owner=owner,
        name="Service Organization",
        slug="service-organization",
    )

    membership = organization.memberships.get(user=owner)
    assert membership.role == OrganizationRole.OWNER
    assert membership.is_active


@pytest.mark.django_db
def test_set_member_role_reactivates_membership() -> None:
    owner = _user("role-owner")
    member = _user("role-member")
    organization = create_organization(
        owner=owner,
        name="Role Organization",
        slug="role-organization",
    )

    membership = set_member_role(
        organization=organization,
        user=member,
        role=OrganizationRole.EDITOR,
    )
    membership.is_active = False
    membership.save(update_fields=("is_active",))

    membership = set_member_role(
        organization=organization,
        user=member,
        role=OrganizationRole.PUBLISHER,
    )

    assert membership.role == OrganizationRole.PUBLISHER
    assert membership.is_active


@pytest.mark.django_db
def test_organization_owner_cannot_be_deactivated() -> None:
    owner = _user("protected-owner")
    organization = create_organization(
        owner=owner,
        name="Protected Organization",
        slug="protected-organization",
    )

    with pytest.raises(ValueError, match="owner cannot be deactivated"):
        deactivate_member(organization=organization, user=owner)
