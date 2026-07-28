from datetime import datetime
from uuid import UUID

from django.db import IntegrityError
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth

from .selectors import organization_for_user, organizations_for_user
from .services import create_organization

router = Router(auth=SessionAuth(), tags=["organizations"])


class OrganizationIn(Schema):
    name: str
    slug: str
    description: str = ""


class OrganizationOut(Schema):
    id: UUID
    name: str
    slug: str
    description: str
    owner_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


@router.get("/", response=list[OrganizationOut])
def list_organizations(request):
    return organizations_for_user(request.auth)


@router.post("/", response={201: OrganizationOut})
def create_organization_endpoint(request, payload: OrganizationIn):
    try:
        organization = create_organization(
            owner=request.auth,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
        )
    except IntegrityError as exc:
        raise HttpError(409, "Organization slug already exists") from exc
    return 201, organization


@router.get("/{slug}", response=OrganizationOut)
def get_organization(request, slug: str):
    organization = organization_for_user(request.auth, slug)
    if organization is None:
        raise HttpError(404, "Organization not found")
    return organization
