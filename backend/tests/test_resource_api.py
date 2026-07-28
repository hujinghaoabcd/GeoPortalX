import json

import pytest

from modules.accounts.models import User
from modules.organizations.models import OrganizationRole
from modules.organizations.services import create_organization, set_member_role
from modules.resources.models import ResourceType, Visibility
from modules.resources.services import create_resource


@pytest.mark.django_db
def test_organization_api_requires_authentication(client):
    response = client.get("/api/v1/organizations/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_user_can_create_and_list_organization(client):
    user = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
    client.force_login(user)

    response = client.post(
        "/api/v1/organizations/",
        data=json.dumps({"name": "Research Lab", "slug": "research-lab"}),
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["slug"] == "research-lab"
    listing = client.get("/api/v1/organizations/")
    assert listing.status_code == 200
    assert [item["slug"] for item in listing.json()] == ["research-lab"]


@pytest.mark.django_db
def test_resource_list_is_permission_filtered(client):
    owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
    viewer = User.objects.create_user(username="viewer", email="viewer@example.com", password="pw")
    create_resource(
        owner=owner,
        resource_type=ResourceType.MAP,
        title="Private map",
        slug="private-map",
        visibility=Visibility.PRIVATE,
    )
    create_resource(
        owner=owner,
        resource_type=ResourceType.MAP,
        title="Public map",
        slug="public-map",
        visibility=Visibility.PUBLIC,
    )

    client.force_login(viewer)
    response = client.get("/api/v1/resources/")

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["public-map"]


@pytest.mark.django_db
def test_editor_can_create_organization_resource(client):
    owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
    editor = User.objects.create_user(username="editor", email="editor@example.com", password="pw")
    organization = create_organization(owner=owner, name="Lab", slug="lab")
    set_member_role(organization=organization, user=editor, role=OrganizationRole.EDITOR)
    client.force_login(editor)

    response = client.post(
        "/api/v1/resources/",
        data=json.dumps(
            {
                "resource_type": ResourceType.VECTOR_DATASET,
                "title": "Road network",
                "slug": "road-network",
                "organization_slug": "lab",
                "visibility": Visibility.ORGANIZATION,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["organization_id"] == str(organization.id)


@pytest.mark.django_db
def test_member_cannot_create_organization_resource(client):
    owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
    member = User.objects.create_user(username="member", email="member@example.com", password="pw")
    organization = create_organization(owner=owner, name="Lab", slug="lab")
    set_member_role(organization=organization, user=member, role=OrganizationRole.MEMBER)
    client.force_login(member)

    response = client.post(
        "/api/v1/resources/",
        data=json.dumps(
            {
                "resource_type": ResourceType.MAP,
                "title": "Unauthorized map",
                "slug": "unauthorized-map",
                "organization_slug": "lab",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 403
