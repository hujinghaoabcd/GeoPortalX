from django.db import transaction

from modules.accounts.models import User
from modules.organizations.models import Organization
from modules.organizations.selectors import can_create_organization_resource

from .models import LifecycleStatus, Resource, ResourceType, Visibility


@transaction.atomic
def create_resource(
    *,
    owner: User,
    resource_type: str,
    title: str,
    slug: str,
    description: str = "",
    visibility: str = Visibility.PRIVATE,
    organization: Organization | None = None,
    metadata: dict | None = None,
) -> Resource:
    """Create a draft resource after enforcing organization and choice invariants."""

    if resource_type not in ResourceType.values:
        raise ValueError(f"Unknown resource type: {resource_type}")
    if visibility not in Visibility.values:
        raise ValueError(f"Unknown visibility: {visibility}")
    if visibility == Visibility.ORGANIZATION and organization is None:
        raise ValueError("Organization visibility requires an organization")
    if organization is not None and not can_create_organization_resource(owner, organization):
        raise PermissionError("User cannot create resources in this organization")

    return Resource.objects.create(
        owner=owner,
        organization=organization,
        resource_type=resource_type,
        title=title,
        slug=slug,
        description=description,
        visibility=visibility,
        lifecycle_status=LifecycleStatus.DRAFT,
        metadata=metadata or {},
    )
