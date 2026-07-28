from typing import Any
from uuid import UUID

from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job
from modules.uploads.models import UploadSession, UploadStatus

from .exceptions import InspectionAuthorizationError, InspectionInputError


def resolve_completed_upload(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> UploadSession:
    """Resolve and authorize the upload referenced by an inspection job."""

    raw_upload_id = parameters.get("upload_id")
    try:
        upload_id = UUID(str(raw_upload_id))
    except (TypeError, ValueError) as exc:
        raise InspectionInputError("input_parameters.upload_id must be a UUID") from exc

    job = Job.objects.select_related("created_by").get(pk=context.job_id)
    try:
        upload = UploadSession.objects.select_related("resource").get(pk=upload_id)
    except UploadSession.DoesNotExist as exc:
        raise InspectionInputError("The requested upload session does not exist") from exc

    if upload.created_by_id != job.created_by_id and not job.created_by.is_superuser:
        raise InspectionAuthorizationError("The job creator does not own this upload session")
    if upload.status != UploadStatus.COMPLETED:
        raise InspectionInputError(f"Upload must be COMPLETED, got {upload.status}")
    if job.resource_id is not None and job.resource_id != upload.resource_id:
        raise InspectionInputError("Job resource does not match the upload resource")
    return upload
