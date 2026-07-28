from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth
from pydantic import Field

from modules.permissions.models import PermissionAction
from modules.resources.selectors import resource_accessible_to

from .models import JobStatus
from .registry import registered_job_types
from .selectors import job_for_user, jobs_for_user
from .services import create_and_dispatch_job, request_job_cancellation

router = Router(auth=SessionAuth(), tags=["jobs"])


class JobIn(Schema):
    job_type: str
    input_parameters: dict[str, Any] = Field(default_factory=dict)
    resource_id: UUID | None = None
    queue: str = "system"
    priority: int = 0
    max_retries: int = 3


class JobOut(Schema):
    id: UUID
    celery_task_id: str
    job_type: str
    queue: str
    status: str
    priority: int
    progress: int
    progress_message: str
    resource_id: UUID | None
    input_parameters: dict[str, Any]
    result_payload: dict[str, Any]
    output_resources: list[Any]
    error_code: str
    error_message: str
    retry_count: int
    max_retries: int
    cancellation_requested_at: datetime | None
    last_heartbeat_at: datetime | None
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobTypeList(Schema):
    job_types: list[str]


@router.get("/types", response=JobTypeList)
def list_job_types(request):
    return {"job_types": list(registered_job_types())}


@router.get("/", response=list[JobOut])
def list_jobs(
    request,
    status: str | None = None,
    job_type: str | None = None,
):
    queryset = jobs_for_user(request.auth)
    if status is not None:
        if status not in JobStatus.values:
            raise HttpError(400, "Unknown job status")
        queryset = queryset.filter(status=status)
    if job_type is not None:
        queryset = queryset.filter(job_type=job_type)
    return queryset


@router.post("/", response={202: JobOut})
def create_job_endpoint(request, payload: JobIn):
    resource = None
    if payload.resource_id is not None:
        resource = resource_accessible_to(
            request.auth,
            payload.resource_id,
            PermissionAction.VIEW,
        )
        if resource is None:
            raise HttpError(404, "Resource not found")

    try:
        job = create_and_dispatch_job(
            created_by=request.auth,
            job_type=payload.job_type,
            input_parameters=payload.input_parameters,
            resource=resource,
            queue=payload.queue,
            priority=payload.priority,
            max_retries=payload.max_retries,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    job.refresh_from_db()
    return 202, job


@router.get("/{job_id}", response=JobOut)
def get_job(request, job_id: UUID):
    job = job_for_user(request.auth, job_id)
    if job is None:
        raise HttpError(404, "Job not found")
    return job


@router.post("/{job_id}/cancel", response=JobOut)
def cancel_job(request, job_id: UUID):
    job = job_for_user(request.auth, job_id)
    if job is None:
        raise HttpError(404, "Job not found")
    try:
        return request_job_cancellation(job=job, requested_by=request.auth)
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
