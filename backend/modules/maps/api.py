from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth
from pydantic import Field

from modules.organizations.models import Organization
from modules.permissions.models import PermissionAction
from modules.resources.models import Visibility

from .models import MapDocument
from .selectors import map_document_accessible_to, map_documents_accessible_to
from .services import (
    activate_map_document_version,
    create_map_document,
    create_map_document_version,
    validate_map_version_sources,
)

router = Router(auth=SessionAuth(), tags=["maps"])


class MapCreateIn(Schema):
    title: str
    slug: str
    description: str = ""
    visibility: str = Visibility.PRIVATE
    organization_id: UUID | None = None
    document: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class MapVersionCreateIn(Schema):
    document: dict[str, Any]
    note: str = ""
    activate: bool = True


class MapVersionActivateIn(Schema):
    note: str = ""


class MapOut(Schema):
    id: UUID
    resource_id: UUID
    title: str
    slug: str
    description: str
    visibility: str
    lifecycle_status: str
    current_version_id: UUID | None
    current_version_number: int | None
    version_count: int
    created_at: datetime
    updated_at: datetime


class MapDetailOut(MapOut):
    current_document: dict[str, Any] | None = None
    versions: list[dict[str, Any]] = Field(default_factory=list)
    version_activations: list[dict[str, Any]] = Field(default_factory=list)
    layer_references: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/", response=list[MapOut])
def list_maps(request):
    return [
        _serialize_map(map_document)
        for map_document in map_documents_accessible_to(request.auth)
    ]


@router.post("/", response={201: MapDetailOut})
def create_map(request, payload: MapCreateIn):
    organization = _organization(payload.organization_id)
    try:
        result = create_map_document(
            actor=request.auth,
            title=payload.title,
            slug=payload.slug,
            description=payload.description,
            visibility=payload.visibility,
            organization=organization,
            document=payload.document,
            note=payload.note,
        )
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return 201, _accessible_map_detail(request.auth, result.map_document.id)


@router.get("/{map_document_id}", response=MapDetailOut)
def get_map(request, map_document_id: UUID):
    map_document = map_document_accessible_to(
        request.auth,
        map_document_id,
        PermissionAction.VIEW,
    )
    if map_document is None:
        raise HttpError(404, "Map document not found")
    return _accessible_map_detail(request.auth, map_document.id)


@router.post("/{map_document_id}/versions", response={201: MapDetailOut})
def create_map_version(
    request,
    map_document_id: UUID,
    payload: MapVersionCreateIn,
):
    map_document = map_document_accessible_to(
        request.auth,
        map_document_id,
        PermissionAction.EDIT,
    )
    if map_document is None:
        raise HttpError(404, "Map document not found")
    try:
        create_map_document_version(
            actor=request.auth,
            map_document_id=map_document.id,
            document=payload.document,
            note=payload.note,
            activate=payload.activate,
        )
    except PermissionError as exc:
        raise HttpError(404, "Map document not found") from exc
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return 201, _accessible_map_detail(request.auth, map_document.id)


@router.post(
    "/{map_document_id}/versions/{version_id}/activate",
    response=MapDetailOut,
)
def activate_map_version(
    request,
    map_document_id: UUID,
    version_id: UUID,
    payload: MapVersionActivateIn,
):
    map_document = map_document_accessible_to(
        request.auth,
        map_document_id,
        PermissionAction.EDIT,
    )
    if map_document is None:
        raise HttpError(404, "Map document not found")
    try:
        activate_map_document_version(
            actor=request.auth,
            map_document_id=map_document.id,
            version_id=version_id,
            note=payload.note,
        )
    except PermissionError as exc:
        raise HttpError(404, "Map document not found") from exc
    except ValueError as exc:
        raise HttpError(409, str(exc)) from exc
    return _accessible_map_detail(request.auth, map_document.id)


def _organization(organization_id: UUID | None) -> Organization | None:
    if organization_id is None:
        return None
    organization = Organization.objects.filter(pk=organization_id, is_active=True).first()
    if organization is None:
        raise HttpError(404, "Organization not found")
    return organization


def _accessible_map_detail(actor, map_document_id: UUID) -> dict[str, Any]:
    map_document = _reload_map(map_document_id)
    if map_document.current_version is not None:
        try:
            validate_map_version_sources(
                actor=actor,
                version=map_document.current_version,
            )
        except (PermissionError, ValueError) as exc:
            raise HttpError(404, "Map document not found") from exc
    return _serialize_map_detail(map_document)


def _reload_map(map_document_id: UUID) -> MapDocument:
    return (
        MapDocument.objects.select_related("resource", "current_version")
        .prefetch_related(
            "versions__created_by",
            "versions__layer_references__dataset__resource",
            "versions__layer_references__dataset_version",
            "version_activations__from_version",
            "version_activations__to_version",
            "version_activations__activated_by",
        )
        .get(pk=map_document_id)
    )


def _serialize_map(map_document: MapDocument) -> dict[str, Any]:
    resource = map_document.resource
    current = map_document.current_version
    annotated_count = getattr(map_document, "version_count", None)
    prefetched = getattr(map_document, "_prefetched_objects_cache", {})
    if annotated_count is not None:
        version_count = int(annotated_count)
    elif "versions" in prefetched:
        version_count = len(prefetched["versions"])
    else:
        version_count = map_document.versions.count()
    return {
        "id": map_document.id,
        "resource_id": resource.id,
        "title": resource.title,
        "slug": resource.slug,
        "description": resource.description,
        "visibility": resource.visibility,
        "lifecycle_status": resource.lifecycle_status,
        "current_version_id": map_document.current_version_id,
        "current_version_number": current.version_number if current else None,
        "version_count": version_count,
        "created_at": map_document.created_at,
        "updated_at": map_document.updated_at,
    }


def _serialize_map_detail(map_document: MapDocument) -> dict[str, Any]:
    payload = _serialize_map(map_document)
    versions = list(map_document.versions.all())
    current = map_document.current_version
    payload["current_document"] = current.document if current else None
    payload["versions"] = [
        {
            "id": str(version.id),
            "version_number": version.version_number,
            "schema_version": version.schema_version,
            "checksum_sha256": version.checksum_sha256,
            "created_by_id": str(version.created_by_id),
            "note": version.note,
            "is_active": map_document.current_version_id == version.id,
            "created_at": version.created_at.isoformat(),
            "activated_at": (
                version.activated_at.isoformat() if version.activated_at else None
            ),
            "deactivated_at": (
                version.deactivated_at.isoformat() if version.deactivated_at else None
            ),
            "activation_count": version.activation_count,
        }
        for version in versions
    ]
    payload["version_activations"] = [
        {
            "id": str(activation.id),
            "from_version_id": (
                str(activation.from_version_id) if activation.from_version_id else None
            ),
            "to_version_id": str(activation.to_version_id),
            "action": activation.action,
            "activated_by_id": str(activation.activated_by_id),
            "note": activation.note,
            "created_at": activation.created_at.isoformat(),
        }
        for activation in map_document.version_activations.all()
    ]
    payload["layer_references"] = []
    if current is not None:
        payload["layer_references"] = [
            {
                "id": str(reference.id),
                "ordinal": reference.ordinal,
                "client_layer_id": reference.client_layer_id,
                "title": reference.title,
                "kind": reference.kind,
                "binding": reference.binding_mode,
                "dataset_id": str(reference.dataset_id),
                "dataset_version_id": (
                    str(reference.dataset_version_id)
                    if reference.dataset_version_id
                    else None
                ),
                "source_layer_name": reference.source_layer_name or None,
                "visible": reference.visible,
                "opacity": reference.opacity,
                "min_zoom": reference.min_zoom,
                "max_zoom": reference.max_zoom,
                "style": reference.style,
                "filter": reference.filter,
                "popup": reference.popup,
                "legend": reference.legend,
            }
            for reference in current.layer_references.all()
        ]
    return payload
