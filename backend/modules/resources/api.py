from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth
from pydantic import Field

from modules.organizations.selectors import organization_for_user
from modules.permissions.models import PermissionAction

from .models import ResourceType, Visibility
from .selectors import resource_accessible_to, resources_accessible_to
from .services import create_resource

router = Router(auth=SessionAuth(), tags=["resources"])


class ResourceIn(Schema):
    resource_type: str
    title: str
    slug: str
    description: str = ""
    visibility: str = Visibility.PRIVATE
    organization_slug: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceOut(Schema):
    id: UUID
    resource_type: str
    title: str
    slug: str
    description: str
    owner_id: UUID
    organization_id: UUID | None
    visibility: str
    lifecycle_status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


@router.get("/", response=list[ResourceOut])
def list_resources(
    request,
    resource_type: str | None = None,
    organization_id: UUID | None = None,
):
    queryset = resources_accessible_to(request.auth)
    if resource_type is not None:
        if resource_type not in ResourceType.values:
            raise HttpError(400, "Unknown resource type")
        queryset = queryset.filter(resource_type=resource_type)
    if organization_id is not None:
        queryset = queryset.filter(organization_id=organization_id)
    return queryset


@router.post("/", response={201: ResourceOut})
def create_resource_endpoint(request, payload: ResourceIn):
    organization = None
    if payload.organization_slug is not None:
        organization = organization_for_user(request.auth, payload.organization_slug)
        if organization is None:
            raise HttpError(404, "Organization not found")

    try:
        resource = create_resource(
            owner=request.auth,
            organization=organization,
            resource_type=payload.resource_type,
            title=payload.title,
            slug=payload.slug,
            description=payload.description,
            visibility=payload.visibility,
            metadata=payload.metadata,
        )
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    except IntegrityError as exc:
        raise HttpError(409, "Resource slug already exists for this owner") from exc
    return 201, resource


@router.get("/{resource_id}", response=ResourceOut)
def get_resource(request, resource_id: UUID):
    resource = resource_accessible_to(request.auth, resource_id, PermissionAction.VIEW)
    if resource is None:
        raise HttpError(404, "Resource not found")
    return resource
