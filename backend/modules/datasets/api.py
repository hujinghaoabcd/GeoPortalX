from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth
from pydantic import Field

from modules.jobs.selectors import job_for_user
from modules.organizations.models import Organization
from modules.permissions.models import PermissionAction
from modules.resources.models import Visibility

from .models import (
    Dataset,
    DatasetKind,
    DatasetStatus,
    DatasetVersionActivationAction,
)
from .selectors import dataset_accessible_to, datasets_accessible_to
from .services import (
    activate_ready_dataset_version,
    register_dataset_from_inspection,
    register_dataset_replacement_from_inspection,
)

router = Router(auth=SessionAuth(), tags=["datasets"])


class DatasetRegisterIn(Schema):
    inspection_job_id: UUID
    title: str
    slug: str
    description: str = ""
    visibility: str = Visibility.PRIVATE
    organization_id: UUID | None = None
    selected_layers: list[str] | None = None
    start_import: bool = True


class DatasetVersionRegisterIn(Schema):
    inspection_job_id: UUID
    selected_layers: list[str] | None = None
    start_import: bool = True


class DatasetVersionActivateIn(Schema):
    note: str = ""


class DatasetOut(Schema):
    id: UUID
    resource_id: UUID
    resource_type: str
    title: str
    slug: str
    description: str
    visibility: str
    lifecycle_status: str
    kind: str
    status: str
    current_version_id: UUID | None
    version_count: int
    import_job_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DatasetDetailOut(DatasetOut):
    versions: list[dict[str, Any]] = Field(default_factory=list)
    version_activations: list[dict[str, Any]] = Field(default_factory=list)
    vector_layers: list[dict[str, Any]] = Field(default_factory=list)
    raster: dict[str, Any] | None = None


@router.get("/", response=list[DatasetOut])
def list_datasets(request, kind: str | None = None, status: str | None = None):
    queryset = datasets_accessible_to(request.auth)
    if kind is not None:
        if kind not in DatasetKind.values:
            raise HttpError(400, "Unknown dataset kind")
        queryset = queryset.filter(kind=kind)
    if status is not None:
        if status not in DatasetStatus.values:
            raise HttpError(400, "Unknown dataset status")
        queryset = queryset.filter(status=status)
    return [_serialize_dataset(dataset) for dataset in queryset]


@router.post("/register", response={200: DatasetDetailOut, 201: DatasetDetailOut})
def register_dataset(request, payload: DatasetRegisterIn):
    inspection_job = job_for_user(request.auth, payload.inspection_job_id)
    if inspection_job is None:
        raise HttpError(404, "Inspection job not found")
    organization = None
    if payload.organization_id is not None:
        organization = Organization.objects.filter(
            pk=payload.organization_id,
            is_active=True,
        ).first()
        if organization is None:
            raise HttpError(404, "Organization not found")
    try:
        registration = register_dataset_from_inspection(
            actor=request.auth,
            inspection_job=inspection_job,
            title=payload.title,
            slug=payload.slug,
            description=payload.description,
            visibility=payload.visibility,
            organization=organization,
            selected_layers=payload.selected_layers,
            start_import=payload.start_import,
        )
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    dataset = _reload_dataset(registration.dataset.pk)
    import_job_id = registration.import_job.id if registration.import_job else None
    body = _serialize_dataset_detail(dataset, import_job_id=import_job_id)
    return (201 if registration.created else 200), body


@router.get("/{dataset_id}", response=DatasetDetailOut)
def get_dataset(request, dataset_id: UUID):
    dataset = dataset_accessible_to(request.auth, dataset_id, PermissionAction.VIEW)
    if dataset is None:
        raise HttpError(404, "Dataset not found")
    return _serialize_dataset_detail(dataset)


@router.post(
    "/{dataset_id}/versions",
    response={200: DatasetDetailOut, 201: DatasetDetailOut},
)
def register_dataset_version(
    request,
    dataset_id: UUID,
    payload: DatasetVersionRegisterIn,
):
    dataset = dataset_accessible_to(request.auth, dataset_id, PermissionAction.EDIT)
    if dataset is None:
        raise HttpError(404, "Dataset not found")
    inspection_job = job_for_user(request.auth, payload.inspection_job_id)
    if inspection_job is None:
        raise HttpError(404, "Inspection job not found")
    try:
        registration = register_dataset_replacement_from_inspection(
            actor=request.auth,
            dataset_id=dataset.id,
            inspection_job=inspection_job,
            selected_layers=payload.selected_layers,
            start_import=payload.start_import,
        )
    except PermissionError as exc:
        raise HttpError(404, "Dataset not found") from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    refreshed = _reload_dataset(dataset.id)
    import_job_id = registration.import_job.id if registration.import_job else None
    body = _serialize_dataset_detail(refreshed, import_job_id=import_job_id)
    return (201 if registration.created else 200), body


@router.post(
    "/{dataset_id}/versions/{version_id}/activate",
    response=DatasetDetailOut,
)
def activate_dataset_version(
    request,
    dataset_id: UUID,
    version_id: UUID,
    payload: DatasetVersionActivateIn,
):
    dataset = dataset_accessible_to(request.auth, dataset_id, PermissionAction.EDIT)
    if dataset is None:
        raise HttpError(404, "Dataset not found")
    target = dataset.versions.filter(pk=version_id).first()
    if target is None:
        raise HttpError(404, "Dataset version not found")

    action = DatasetVersionActivationAction.MANUAL
    if (
        dataset.current_version is not None
        and target.version_number < dataset.current_version.version_number
    ):
        action = DatasetVersionActivationAction.ROLLBACK
    try:
        activate_ready_dataset_version(
            actor=request.auth,
            dataset_id=dataset.id,
            version_id=target.id,
            requested_action=action,
            note=payload.note,
        )
    except PermissionError as exc:
        raise HttpError(404, "Dataset not found") from exc
    except ValueError as exc:
        raise HttpError(409, str(exc)) from exc
    return _serialize_dataset_detail(_reload_dataset(dataset.id))


def _reload_dataset(dataset_id: UUID) -> Dataset:
    return (
        Dataset.objects.select_related("resource", "current_version")
        .prefetch_related(
            "versions",
            "vector__layers",
            "version_activations__from_version",
            "version_activations__to_version",
            "version_activations__activated_by",
        )
        .get(pk=dataset_id)
    )


def _serialize_dataset(
    dataset: Dataset,
    *,
    import_job_id: UUID | None = None,
) -> dict[str, Any]:
    resource = dataset.resource
    return {
        "id": dataset.id,
        "resource_id": resource.id,
        "resource_type": resource.resource_type,
        "title": resource.title,
        "slug": resource.slug,
        "description": resource.description,
        "visibility": resource.visibility,
        "lifecycle_status": resource.lifecycle_status,
        "kind": dataset.kind,
        "status": dataset.status,
        "current_version_id": dataset.current_version_id,
        "version_count": _version_count(dataset),
        "import_job_id": import_job_id,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def _serialize_dataset_detail(
    dataset: Dataset,
    *,
    import_job_id: UUID | None = None,
) -> dict[str, Any]:
    payload = _serialize_dataset(dataset, import_job_id=import_job_id)
    payload["versions"] = [
        {
            "id": str(version.id),
            "version_number": version.version_number,
            "status": version.status,
            "is_active": dataset.current_version_id == version.id,
            "source_upload_id": str(version.source_upload_id),
            "inspection_job_id": str(version.inspection_job_id),
            "source_format": version.source_format,
            "source_checksum_sha256": version.source_checksum_sha256,
            "failure_code": version.failure_code,
            "failure_message": version.failure_message,
            "created_at": version.created_at.isoformat(),
            "imported_at": (
                version.imported_at.isoformat() if version.imported_at else None
            ),
            "activated_at": (
                version.activated_at.isoformat() if version.activated_at else None
            ),
            "deactivated_at": (
                version.deactivated_at.isoformat()
                if version.deactivated_at
                else None
            ),
            "activation_count": version.activation_count,
        }
        for version in dataset.versions.all()
    ]
    payload["version_activations"] = [
        {
            "id": str(activation.id),
            "from_version_id": (
                str(activation.from_version_id)
                if activation.from_version_id
                else None
            ),
            "to_version_id": str(activation.to_version_id),
            "action": activation.action,
            "activated_by_id": str(activation.activated_by_id),
            "note": activation.note,
            "created_at": activation.created_at.isoformat(),
        }
        for activation in dataset.version_activations.all()
    ]
    payload["vector_layers"] = []
    payload["raster"] = None
    if dataset.kind == DatasetKind.VECTOR:
        payload["vector_layers"] = [
            {
                "id": str(layer.id),
                "version_id": str(layer.version_id),
                "is_active": dataset.current_version_id == layer.version_id,
                "name": layer.source_layer_name,
                "title": layer.title,
                "status": layer.status,
                "geometry_type": layer.geometry_type,
                "geometry_column": layer.geometry_column,
                "srid": layer.srid,
                "feature_count": layer.feature_count,
                "field_schema": layer.field_schema,
                "field_statistics": layer.field_statistics,
                "quality_report": layer.quality_report,
                "db_schema": layer.db_schema,
                "db_table": layer.db_table,
                "tile_source_id": layer.tile_source_id,
                "min_zoom": layer.min_zoom,
                "max_zoom": layer.max_zoom,
                "tilejson_path": (
                    f"/api/v1/vector-layers/{layer.id}/tilejson"
                    if layer.tile_source_id
                    and dataset.current_version_id == layer.version_id
                    else None
                ),
                "source_path": (
                    f"/api/v1/vector-layers/{layer.id}/source"
                    if layer.tile_source_id
                    and dataset.current_version_id == layer.version_id
                    else None
                ),
                "quality_path": (
                    f"/api/v1/vector-layers/{layer.id}/quality"
                    if layer.tile_source_id
                    and dataset.current_version_id == layer.version_id
                    else None
                ),
                "failure_code": layer.failure_code,
                "failure_message": layer.failure_message,
            }
            for layer in dataset.vector.layers.all()
        ]
    elif hasattr(dataset, "raster"):
        raster = dataset.raster
        payload["raster"] = {
            "width": raster.width,
            "height": raster.height,
            "band_count": raster.band_count,
            "driver": raster.driver,
            "crs": raster.crs,
            "epsg": raster.epsg,
            "source_bounds": raster.source_bounds,
            "bands": raster.bands,
            "image_structure": raster.image_structure,
            "cog_readiness": raster.cog_readiness,
            "published_bucket": raster.published_bucket,
            "published_key": raster.published_key,
        }
    return payload


def _version_count(dataset: Dataset) -> int:
    annotated = getattr(dataset, "version_count", None)
    return int(annotated) if annotated is not None else dataset.versions.count()
