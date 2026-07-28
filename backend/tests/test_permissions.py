from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from modules.accounts.models import User
from modules.organizations.models import Organization, OrganizationMember
from modules.permissions.models import (
    PermissionAction,
    PermissionSubjectType,
    ResourcePermission,
)
from modules.permissions.services import has_resource_permission
from modules.resources.models import Resource, ResourceType, Visibility


def _user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def _resource(owner: User, **kwargs) -> Resource:
    return Resource.objects.create(
        owner=owner,
        title=kwargs.pop("title", "Test resource"),
        slug=kwargs.pop("slug", "test-resource"),
        resource_type=kwargs.pop("resource_type", ResourceType.MAP),
        **kwargs,
    )


@pytest.mark.django_db
def test_owner_has_all_resource_permissions() -> None:
    owner = _user("owner")
    resource = _resource(owner)

    assert has_resource_permission(owner, resource, PermissionAction.MANAGE)
    assert has_resource_permission(owner, resource, PermissionAction.DOWNLOAD)


@pytest.mark.django_db
def test_public_visibility_only_grants_view() -> None:
    owner = _user("public-owner")
    resource = _resource(owner, visibility=Visibility.PUBLIC)
    anonymous = AnonymousUser()

    assert has_resource_permission(anonymous, resource, PermissionAction.VIEW)
    assert not has_resource_permission(anonymous, resource, PermissionAction.DOWNLOAD)


@pytest.mark.django_db
def test_organization_member_can_view_organization_resource() -> None:
    owner = _user("organization-owner")
    member = _user("organization-member")
    organization = Organization.objects.create(
        owner=owner,
        name="Example Organization",
        slug="example-organization",
    )
    OrganizationMember.objects.create(organization=organization, user=member)
    resource = _resource(
        owner,
        organization=organization,
        visibility=Visibility.ORGANIZATION,
    )

    assert has_resource_permission(member, resource, PermissionAction.VIEW)
    assert not has_resource_permission(member, resource, PermissionAction.EDIT)


@pytest.mark.django_db
def test_edit_grant_implies_view_and_download() -> None:
    owner = _user("grant-owner")
    editor = _user("grant-editor")
    resource = _resource(owner)
    ResourcePermission.objects.create(
        resource=resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=editor.id,
        action=PermissionAction.EDIT,
        granted_by=owner,
    )

    assert has_resource_permission(editor, resource, PermissionAction.VIEW)
    assert has_resource_permission(editor, resource, PermissionAction.DOWNLOAD)
    assert has_resource_permission(editor, resource, PermissionAction.EDIT)
    assert not has_resource_permission(editor, resource, PermissionAction.PUBLISH)


@pytest.mark.django_db
def test_expired_grant_is_ignored() -> None:
    owner = _user("expired-owner")
    viewer = _user("expired-viewer")
    resource = _resource(owner)
    ResourcePermission.objects.create(
        resource=resource,
        subject_type=PermissionSubjectType.USER,
        subject_id=viewer.id,
        action=PermissionAction.VIEW,
        granted_by=owner,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert not has_resource_permission(viewer, resource, PermissionAction.VIEW)
