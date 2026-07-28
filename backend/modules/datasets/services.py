from dataclasses import dataclass
from typing import Any

from django.db import transaction

from modules.accounts.models import User
from modules.jobs.models import Job, JobStatus
from modules.jobs.services import create_and_dispatch_job
from modules.organizations.models import Organization
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission
from modules.resources.models import LifecycleStatus, ResourceType, Visibility
from modules.resources.services import create_resource
from modules.uploads.models import UploadSession, UploadStatus

from .models import (
    Dataset,
    DatasetKind,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    RasterDataset,
    VectorDataset,
    VectorLayer,
)


@dataclass(frozen=True, slots=True)
class DatasetRegistration:
    dataset: Dataset
    version: DatasetVersion
    import_job: Job | None
    created: bool


_INSPECTION_JOB_TYPES = {
    "vector-inspect": DatasetKind.VECTOR,
    "raster-inspect": DatasetKind.RASTER,
}
_RESOURCE_TYPES = {
    DatasetKind.VECTOR: ResourceType.VECTOR_DATASET,
    DatasetKind.RASTER: ResourceType.RASTER_DATASET,
}


@transaction.atomic
def register_dataset_from_inspection(
    *,
    actor: User,
    inspection_job: Job,
    title: str,
    slug: str,
    description: str = "",
    visibility: str = Visibility.PRIVATE,
    organization: Organization | None = None,
    selected_layers: list[str] | None = None,
    start_import: bool = True,
) -> DatasetRegistration:
    """Convert one successful inspection into an immutable dataset version."""

    kind = _validate_inspection_job(actor=actor, inspection_job=inspection_job)
    upload, inspection = _resolve_inspection_payload(inspection_job)
    existing = (
        DatasetVersion.objects.select_related("dataset", "dataset__resource")
        .filter(source_upload=upload)
        .first()
    )
    if existing is not None:
        return DatasetRegistration(
            dataset=existing.dataset,
            version=existing,
            import_job=_active_import_job(existing),
            created=False,
        )

    resource = _resolve_or_create_resource(
        actor=actor,
        upload=upload,
        kind=kind,
        title=title,
        slug=slug,
        description=description,
        visibility=visibility,
        organization=organization,
    )
    dataset = Dataset.objects.create(
        resource=resource,
        kind=kind,
        status=DatasetStatus.REGISTERED,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        source_upload=upload,
        inspection_job=inspection_job,
        source_format=str(inspection.get("format", "")),
        source_checksum_sha256=str(
            inspection_job.result_payload["upload"].get("sha256", "")
        ),
        inspection_result=inspection,
        created_by=actor,
    )
    dataset.current_version = version
    dataset.save(update_fields=("current_version", "updated_at"))

    import_job: Job | None = None
    if kind == DatasetKind.VECTOR:
        _register_vector_dataset(
            dataset=dataset,
            version=version,
            inspection=inspection,
            selected_layers=selected_layers,
        )
        if start_import:
            import_job = _queue_vector_import(actor=actor, dataset=dataset, version=version)
    else:
        _register_raster_dataset(dataset=dataset, inspection=inspection)
        dataset.status = DatasetStatus.READY
        dataset.save(update_fields=("status", "updated_at"))
        version.status = DatasetVersionStatus.READY
        version.save(update_fields=("status",))
        resource.lifecycle_status = LifecycleStatus.READY
        resource.save(update_fields=("lifecycle_status", "updated_at"))

    return DatasetRegistration(
        dataset=dataset,
        version=version,
        import_job=import_job,
        created=True,
    )


def _validate_inspection_job(*, actor: User, inspection_job: Job) -> str:
    if inspection_job.created_by_id != actor.id and not actor.is_superuser:
        raise PermissionError("Inspection job is not owned by the current user")
    if inspection_job.status != JobStatus.SUCCEEDED:
        raise ValueError("Inspection job must be successful before registration")
    try:
        return _INSPECTION_JOB_TYPES[inspection_job.job_type]
    except KeyError as exc:
        raise ValueError("Job is not a supported dataset inspection") from exc


def _resolve_inspection_payload(inspection_job: Job) -> tuple[UploadSession, dict[str, Any]]:
    payload = inspection_job.result_payload
    upload_payload = payload.get("upload")
    inspection = payload.get("inspection")
    if not isinstance(upload_payload, dict) or not isinstance(inspection, dict):
        raise ValueError("Inspection result payload is incomplete")
    upload_id = upload_payload.get("id")
    if not upload_id:
        raise ValueError("Inspection result does not identify its upload")
    try:
        upload = UploadSession.objects.select_related("resource", "created_by").get(pk=upload_id)
    except (UploadSession.DoesNotExist, ValueError) as exc:
        raise ValueError("Inspection upload no longer exists") from exc
    if upload.status != UploadStatus.COMPLETED:
        raise ValueError("Only completed uploads can be registered")
    if upload.created_by_id != inspection_job.created_by_id:
        raise ValueError("Inspection job and upload owners do not match")
    if inspection_job.resource_id != upload.resource_id:
        raise ValueError("Inspection job and upload resources do not match")
    return upload, inspection


def _resolve_or_create_resource(
    *,
    actor: User,
    upload: UploadSession,
    kind: str,
    title: str,
    slug: str,
    description: str,
    visibility: str,
    organization: Organization | None,
):
    expected_type = _RESOURCE_TYPES[kind]
    if upload.resource is not None:
        resource = upload.resource
        if resource.resource_type != expected_type:
            raise ValueError("Upload resource type does not match the inspected dataset")
        if not has_resource_permission(actor, resource, PermissionAction.EDIT):
            raise PermissionError("User cannot register this resource")
        if hasattr(resource, "dataset"):
            raise ValueError("Resource already has a dataset registration")
        return resource
    return create_resource(
        owner=actor,
        resource_type=expected_type,
        title=title,
        slug=slug,
        description=description,
        visibility=visibility,
        organization=organization,
        metadata={"source_upload_id": str(upload.id)},
    )


def _register_vector_dataset(
    *,
    dataset: Dataset,
    version: DatasetVersion,
    inspection: dict[str, Any],
    selected_layers: list[str] | None,
) -> None:
    raw_layers = inspection.get("layers")
    if not isinstance(raw_layers, list):
        raise ValueError("Vector inspection does not contain a layer list")
    spatial_layers = [
        layer
        for layer in raw_layers
        if isinstance(layer, dict) and layer.get("geometry_type") is not None
    ]
    available = {str(layer.get("name")): layer for layer in spatial_layers}
    names = selected_layers or list(available)
    if not names:
        raise ValueError("Vector dataset contains no spatial layers")
    if len(names) != len(set(names)):
        raise ValueError("Selected vector layers must be unique")
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise ValueError(f"Unknown or nonspatial vector layers: {', '.join(unknown)}")

    vector_dataset = VectorDataset.objects.create(dataset=dataset, layer_count=len(names))
    for ordinal, name in enumerate(names, start=1):
        layer = available[name]
        VectorLayer.objects.create(
            vector_dataset=vector_dataset,
            version=version,
            ordinal=ordinal,
            source_layer_name=name,
            title=name,
            source_driver=str(layer.get("driver") or ""),
            source_crs=str(layer.get("crs") or ""),
            source_bounds=layer.get("bounds") or [],
            field_schema=layer.get("fields") or [],
            geometry_type=str(layer.get("geometry_type") or ""),
            feature_count=max(int(layer.get("feature_count", 0)), 0),
        )


def _register_raster_dataset(*, dataset: Dataset, inspection: dict[str, Any]) -> None:
    RasterDataset.objects.create(
        dataset=dataset,
        width=int(inspection.get("width", 0)),
        height=int(inspection.get("height", 0)),
        band_count=int(inspection.get("band_count", 0)),
        driver=str(inspection.get("driver") or ""),
        crs=str(inspection.get("crs") or ""),
        epsg=inspection.get("epsg"),
        source_bounds=inspection.get("bounds") or [],
        transform=inspection.get("transform") or [],
        bands=inspection.get("bands") or [],
        image_structure=inspection.get("image_structure") or {},
        cog_readiness=inspection.get("cog_readiness") or {},
    )


def _queue_vector_import(*, actor: User, dataset: Dataset, version: DatasetVersion) -> Job:
    dataset.status = DatasetStatus.IMPORTING
    dataset.failure_code = ""
    dataset.failure_message = ""
    dataset.save(update_fields=("status", "failure_code", "failure_message", "updated_at"))
    version.status = DatasetVersionStatus.IMPORTING
    version.save(update_fields=("status", "failure_code", "failure_message"))
    resource = dataset.resource
    resource.lifecycle_status = LifecycleStatus.PROCESSING
    resource.save(update_fields=("lifecycle_status", "updated_at"))
    return create_and_dispatch_job(
        created_by=actor,
        job_type="vector-import",
        input_parameters={"dataset_version_id": str(version.id)},
        resource=resource,
        queue="vector",
        max_retries=1,
    )


def _active_import_job(version: DatasetVersion) -> Job | None:
    return (
        Job.objects.filter(
            resource=version.dataset.resource,
            job_type="vector-import",
            input_parameters__dataset_version_id=str(version.id),
        )
        .order_by("-created_at")
        .first()
    )
