from uuid import UUID

from django.db.models import Count, QuerySet

from modules.accounts.models import User
from modules.permissions.models import PermissionAction
from modules.resources.selectors import resources_accessible_to

from .models import Dataset


def datasets_accessible_to(
    user: User,
    required_action: str = PermissionAction.VIEW,
) -> QuerySet[Dataset]:
    resource_ids = resources_accessible_to(user, required_action).values("id")
    return (
        Dataset.objects.filter(resource_id__in=resource_ids)
        .select_related(
            "resource",
            "resource__owner",
            "resource__organization",
            "current_version",
        )
        .prefetch_related("versions", "vector__layers")
        .annotate(version_count=Count("versions", distinct=True))
    )


def dataset_accessible_to(
    user: User,
    dataset_id: UUID,
    required_action: str = PermissionAction.VIEW,
) -> Dataset | None:
    return datasets_accessible_to(user, required_action).filter(pk=dataset_id).first()
