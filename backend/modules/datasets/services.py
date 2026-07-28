from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

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
    DatasetVersionActivation,
    DatasetVersionActivationAction,
    DatasetVersionStatus,
    RasterDataset,
    VectorDataset,
    VectorLayer,
    VectorLayerStatus,
)


@dataclass(frozen=True, slots=True)
class DatasetRegistration:
    dataset: Dataset
    version: DatasetVersion
    import_job: Job | None
    created: bool


@dataclass(frozen=True, slots=True)
class DatasetVersionActivationResult:
    dataset: Dataset
    version: DatasetVersion
    previous_version: DatasetVersion | None
    activation: DatasetVersionActivation | None
    changed: bool


_INSPECTION_JOB_TYPES = {
    "vector-inspect": DatasetKind.VECTOR,
    "raster-inspect": DatasetKind.RASTER,
}
_RESOURCE_TYPES = {
    DatasetKind.VECTOR: ResourceType.VECTOR_DATASET,
    DatasetKind.RASTER: ResourceType.RASTER_DATASET,
}
_PENDING_VERSION_STATUSES = {
    DatasetVersionStatus.REGISTERED,
    DatasetVersionStatus.IMPORTING,
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
    """Convert one successful inspection into the first immutable dataset version."""

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
    version = _create_version(
        dataset=dataset,
        actor=actor,
        inspection_job=inspection_job,
        upload=upload,
        inspection=inspection,
        version_number=1,
    )

    import_job: Job | None = None
    if kind == DatasetKind.VECTOR:
        vector_dataset = VectorDataset.objects.create(dataset=dataset)
        _register_vector_layers(
            vector_dataset=vector_dataset,
            version=version,
            inspection=inspection,
            selected_layers=selected_layers,
        )
        if start_import:
            import_job = _queue_vector_import(
                actor=actor,
                dataset=dataset,
                version=version,
            )
    else:
        _register_raster_dataset(dataset=dataset, inspection=inspection)
        version.status = DatasetVersionStatus.READY
        version.imported_at = timezone.now()
        version.save(update_fields=("status", "imported_at"))
        activate_ready_dataset_version(
            actor=actor,
            dataset_id=dataset.id,
            version_id=version.id,
            requested_action=DatasetVersionActivationAction.INITIAL,
            note="Initial raster registration",
        )

    return DatasetRegistration(
        dataset=dataset,
        version=version,
        import_job=import_job,
        created=True,
    )


@transaction.atomic
def register_dataset_replacement_from_inspection(
    *,
    actor: User,
    dataset_id: UUID,
    inspection_job: Job,
    selected_layers: list[str] | None = None,
    start_import: bool = True,
) -> DatasetRegistration:
    """Register a candidate version without disturbing the active version."""

    dataset = (
        Dataset.objects.select_for_update()
        .select_related("resource", "current_version")
        .get(pk=dataset_id)
    )
    if not has_resource_permission(actor, dataset.resource, PermissionAction.EDIT):
        raise PermissionError("User cannot replace this dataset")
    if dataset.kind != DatasetKind.VECTOR:
        raise ValueError("Dataset replacement currently supports vector datasets only")

    kind = _validate_inspection_job(actor=actor, inspection_job=inspection_job)
    if kind != dataset.kind:
        raise ValueError("Inspection type does not match the dataset kind")
    upload, inspection = _resolve_inspection_payload(inspection_job)
    if upload.resource_id != dataset.resource_id:
        raise ValueError(
            "Replacement upload must be created against the existing dataset resource"
        )

    existing = (
        DatasetVersion.objects.select_related("dataset")
        .filter(source_upload=upload)
        .first()
    )
    if existing is not None:
        if existing.dataset_id != dataset.id:
            raise ValueError("Upload is already registered to another dataset")
        return DatasetRegistration(
            dataset=dataset,
            version=existing,
            import_job=_active_import_job(existing),
            created=False,
        )

    pending = dataset.versions.filter(status__in=_PENDING_VERSION_STATUSES)
    if dataset.current_version_id is not None:
        pending = pending.exclude(pk=dataset.current_version_id)
    if pending.exists():
        raise ValueError("Dataset already has a pending replacement version")

    maximum = dataset.versions.aggregate(value=Max("version_number"))["value"] or 0
    version = _create_version(
        dataset=dataset,
        actor=actor,
        inspection_job=inspection_job,
        upload=upload,
        inspection=inspection,
        version_number=int(maximum) + 1,
    )
    _register_vector_layers(
        vector_dataset=dataset.vector,
        version=version,
        inspection=inspection,
        selected_layers=selected_layers,
    )

    import_job = None
    if start_import:
        import_job = _queue_vector_import(
            actor=actor,
            dataset=dataset,
            version=version,
        )
    return DatasetRegistration(
        dataset=dataset,
        version=version,
        import_job=import_job,
        created=True,
    )


@transaction.atomic
def activate_ready_dataset_version(
    *,
    actor: User,
    dataset_id: UUID,
    version_id: UUID,
    requested_action: str | None = None,
    note: str = "",
) -> DatasetVersionActivationResult:
    """Atomically publish a ready version or roll back to a ready historical version."""

    dataset = (
        Dataset.objects.select_for_update()
        .select_related("resource", "current_version")
        .get(pk=dataset_id)
    )
    if not has_resource_permission(actor, dataset.resource, PermissionAction.EDIT):
        raise PermissionError("User cannot activate this dataset version")
    version = (
        DatasetVersion.objects.select_for_update()
        .select_related("dataset")
        .get(pk=version_id, dataset=dataset)
    )
    if version.status != DatasetVersionStatus.READY:
        raise ValueError("Only ready dataset versions can be activated")

    layers = list(version.vector_layers.all()) if dataset.kind == DatasetKind.VECTOR else []
    if dataset.kind == DatasetKind.VECTOR:
        if not layers or any(
            layer.status != VectorLayerStatus.READY
            or not layer.db_schema
            or not layer.db_table
            or not layer.tile_source_id
            for layer in layers
        ):
            raise ValueError("All version layers must be ready before activation")

    already_finalized = (
        dataset.current_version_id == version.id
        and dataset.status == DatasetStatus.READY
        and dataset.resource.lifecycle_status == LifecycleStatus.READY
        and version.activation_count > 0
    )
    if already_finalized:
        return DatasetVersionActivationResult(
            dataset=dataset,
            version=version,
            previous_version=version,
            activation=None,
            changed=False,
        )

    previous = (
        dataset.current_version
        if dataset.current_version_id != version.id
        else None
    )
    action = _activation_action(
        previous=previous,
        target=version,
        requested=requested_action,
    )
    now = timezone.now()
    if previous is not None:
        previous.deactivated_at = now
        previous.save(update_fields=("deactivated_at",))

    version.activated_at = now
    version.deactivated_at = None
    version.activation_count += 1
    version.save(
        update_fields=(
            "activated_at",
            "deactivated_at",
            "activation_count",
        )
    )

    dataset.current_version = version
    dataset.status = DatasetStatus.READY
    dataset.failure_code = ""
    dataset.failure_message = ""
    dataset.save(
        update_fields=(
            "current_version",
            "status",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )

    if dataset.kind == DatasetKind.VECTOR:
        dataset.vector.layer_count = len(layers)
        dataset.vector.imported_layer_count = len(layers)
        dataset.vector.save(update_fields=("layer_count", "imported_layer_count"))

    dataset.resource.lifecycle_status = LifecycleStatus.READY
    dataset.resource.spatial_extent = _version_extent(dataset=dataset, layers=layers)
    dataset.resource.save(
        update_fields=("lifecycle_status", "spatial_extent", "updated_at")
    )

    activation = DatasetVersionActivation.objects.create(
        dataset=dataset,
        from_version=previous,
        to_version=version,
        action=action,
        activated_by=actor,
        note=str(note or "")[:500],
    )
    return DatasetVersionActivationResult(
        dataset=dataset,
        version=version,
        previous_version=previous,
        activation=activation,
        changed=True,
    )


def _create_version(
    *,
    dataset: Dataset,
    actor: User,
    inspection_job: Job,
    upload: UploadSession,
    inspection: dict[str, Any],
    version_number: int,
) -> DatasetVersion:
    return DatasetVersion.objects.create(
        dataset=dataset,
        version_number=version_number,
        source_upload=upload,
        inspection_job=inspection_job,
        source_format=str(inspection.get("format", "")),
        source_checksum_sha256=str(
            inspection_job.result_payload["upload"].get("sha256", "")
        ),
        inspection_result=inspection,
        created_by=actor,
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


def _resolve_inspection_payload(
    inspection_job: Job,
) -> tuple[UploadSession, dict[str, Any]]:
    payload = inspection_job.result_payload
    upload_payload = payload.get("upload")
    inspection = payload.get("inspection")
    if not isinstance(upload_payload, dict) or not isinstance(inspection, dict):
        raise ValueError("Inspection result payload is incomplete")
    upload_id = upload_payload.get("id")
    if not upload_id:
        raise ValueError("Inspection result does not identify its upload")
    try:
        upload = UploadSession.objects.select_related("resource", "created_by").get(
            pk=upload_id
        )
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


def _register_vector_layers(
    *,
    vector_dataset: VectorDataset,
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


def _queue_vector_import(
    *,
    actor: User,
    dataset: Dataset,
    version: DatasetVersion,
) -> Job:
    has_active_version = dataset.current_version_id is not None
    if not has_active_version:
        dataset.status = DatasetStatus.IMPORTING
        dataset.failure_code = ""
        dataset.failure_message = ""
        dataset.save(
            update_fields=(
                "status",
                "failure_code",
                "failure_message",
                "updated_at",
            )
        )
        resource = dataset.resource
        resource.lifecycle_status = LifecycleStatus.PROCESSING
        resource.save(update_fields=("lifecycle_status", "updated_at"))

    version.status = DatasetVersionStatus.IMPORTING
    version.failure_code = ""
    version.failure_message = ""
    version.save(update_fields=("status", "failure_code", "failure_message"))
    return create_and_dispatch_job(
        created_by=actor,
        job_type="vector-import",
        input_parameters={"dataset_version_id": str(version.id)},
        resource=dataset.resource,
        queue="vector",
        max_retries=1,
    )


def _activation_action(
    *,
    previous: DatasetVersion | None,
    target: DatasetVersion,
    requested: str | None,
) -> str:
    if requested is not None:
        if requested not in DatasetVersionActivationAction.values:
            raise ValueError("Unknown dataset version activation action")
        return requested
    if previous is None:
        return DatasetVersionActivationAction.INITIAL
    if target.version_number > previous.version_number:
        return DatasetVersionActivationAction.REPLACEMENT
    if target.version_number < previous.version_number:
        return DatasetVersionActivationAction.ROLLBACK
    return DatasetVersionActivationAction.MANUAL


def _version_extent(*, dataset: Dataset, layers: list[VectorLayer]):
    if dataset.kind == DatasetKind.VECTOR:
        return _union_extents([layer.extent for layer in layers if layer.extent is not None])
    return dataset.resource.spatial_extent


def _union_extents(extents):
    if not extents:
        return None
    from django.contrib.gis.geos import Polygon

    min_x = min(extent.extent[0] for extent in extents)
    min_y = min(extent.extent[1] for extent in extents)
    max_x = max(extent.extent[2] for extent in extents)
    max_y = max(extent.extent[3] for extent in extents)
    polygon = Polygon.from_bbox((min_x, min_y, max_x, max_y))
    polygon.srid = 4326
    return polygon


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
