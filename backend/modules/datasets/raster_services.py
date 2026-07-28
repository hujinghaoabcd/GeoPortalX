from dataclasses import dataclass
from uuid import UUID

from django.db import transaction

from modules.accounts.models import User
from modules.jobs.models import Job, JobStatus
from modules.jobs.services import create_and_dispatch_job
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission

from .models import (
    Dataset,
    DatasetKind,
    RasterPublication,
    RasterPublicationStatus,
)


@dataclass(frozen=True, slots=True)
class RasterPublicationRequest:
    publication: RasterPublication
    job: Job | None
    created: bool


_ACTIVE_JOB_STATUSES = {
    JobStatus.PENDING,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.RETRYING,
}


@transaction.atomic
def request_raster_publication(*, actor: User, dataset_id: UUID) -> RasterPublicationRequest:
    dataset = (
        Dataset.objects.select_for_update()
        .select_related("resource", "current_version", "raster")
        .get(pk=dataset_id)
    )
    if dataset.kind != DatasetKind.RASTER:
        raise ValueError("Dataset is not a raster dataset")
    if not has_resource_permission(actor, dataset.resource, PermissionAction.EDIT):
        raise PermissionError("User cannot publish this raster dataset")
    if dataset.current_version is None:
        raise ValueError("Raster dataset has no active source version")

    publication, created = RasterPublication.objects.select_for_update().get_or_create(
        version=dataset.current_version,
        defaults={
            "raster_dataset": dataset.raster,
            "created_by": actor,
        },
    )
    if publication.status == RasterPublicationStatus.READY:
        return RasterPublicationRequest(publication=publication, job=publication.job, created=False)
    if publication.job and publication.job.status in _ACTIVE_JOB_STATUSES:
        return RasterPublicationRequest(publication=publication, job=publication.job, created=False)

    publication.status = RasterPublicationStatus.PENDING
    publication.failure_code = ""
    publication.failure_message = ""
    publication.started_at = None
    publication.completed_at = None
    publication.save(
        update_fields=(
            "status",
            "failure_code",
            "failure_message",
            "started_at",
            "completed_at",
            "updated_at",
        )
    )
    job = create_and_dispatch_job(
        created_by=actor,
        job_type="raster-publish",
        input_parameters={"raster_publication_id": str(publication.id)},
        resource=dataset.resource,
        queue="raster",
        max_retries=1,
    )
    publication.job = job
    publication.save(update_fields=("job", "updated_at"))
    return RasterPublicationRequest(publication=publication, job=job, created=created)


def ensure_initial_raster_publication(dataset_id: UUID) -> None:
    dataset = Dataset.objects.select_related("current_version", "resource").filter(
        pk=dataset_id,
        kind=DatasetKind.RASTER,
    ).first()
    if dataset is None or dataset.current_version_id is None:
        return
    request_raster_publication(actor=dataset.current_version.created_by, dataset_id=dataset.id)
