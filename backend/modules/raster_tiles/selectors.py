from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.db.models import F, QuerySet

from modules.accounts.models import User
from modules.datasets.models import RasterPublication, RasterPublicationStatus
from modules.permissions.models import PermissionAction
from modules.resources.selectors import resources_accessible_to


def raster_publications_accessible_to(
    user: User | AnonymousUser,
    required_action: str = PermissionAction.VIEW,
    *,
    ready_only: bool = True,
) -> QuerySet[RasterPublication]:
    accessible_resources = resources_accessible_to(user, required_action).values("pk")
    queryset = RasterPublication.objects.select_related(
        "version",
        "raster_dataset",
        "raster_dataset__dataset",
        "raster_dataset__dataset__resource",
        "raster_dataset__dataset__current_version",
        "render_settings",
        "job",
    ).filter(
        version_id=F("raster_dataset__dataset__current_version_id"),
        raster_dataset__dataset__resource_id__in=accessible_resources,
    )
    if ready_only:
        queryset = queryset.filter(
            status=RasterPublicationStatus.READY,
            object_key__gt="",
        )
    return queryset


def raster_publication_accessible_to(
    user: User | AnonymousUser,
    dataset_id: UUID,
    required_action: str = PermissionAction.VIEW,
    *,
    ready_only: bool = True,
) -> RasterPublication | None:
    return raster_publications_accessible_to(
        user,
        required_action,
        ready_only=ready_only,
    ).filter(raster_dataset_id=dataset_id).first()
