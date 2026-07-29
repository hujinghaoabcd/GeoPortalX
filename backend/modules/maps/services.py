import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from modules.accounts.models import User
from modules.datasets.models import (
    Dataset,
    DatasetKind,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    RasterPublication,
    RasterPublicationStatus,
    VectorLayer,
    VectorLayerStatus,
)
from modules.organizations.models import Organization
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission
from modules.resources.models import LifecycleStatus, Resource, ResourceType, Visibility
from modules.resources.selectors import resources_accessible_to
from modules.resources.services import create_resource

from .document_schema import MapDocumentSchema, validate_map_document
from .models import (
    MapDocument,
    MapDocumentVersion,
    MapDocumentVersionActivation,
    MapLayerBindingMode,
    MapLayerReference,
    MapVersionActivationAction,
)


@dataclass(frozen=True)
class MapDocumentWriteResult:
    map_document: MapDocument
    version: MapDocumentVersion


def _document_checksum(document: dict[str, Any]) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_note(note: str) -> str:
    normalized = note.strip()
    if len(normalized) > 500:
        raise ValueError("Map version notes must not exceed 500 characters")
    return normalized


def _validate_source_references(
    *,
    actor: User,
    parsed: MapDocumentSchema,
) -> dict[str, DatasetVersion]:
    dataset_ids = {layer.dataset_id for layer in parsed.layers}
    datasets = {
        dataset.id: dataset
        for dataset in Dataset.objects.filter(id__in=dataset_ids).select_related(
            "resource",
            "current_version",
        )
    }
    if dataset_ids.difference(datasets):
        raise ValueError("One or more referenced datasets do not exist")

    source_resource_ids = {dataset.resource_id for dataset in datasets.values()}
    accessible_resource_ids = set(
        resources_accessible_to(actor, PermissionAction.VIEW)
        .filter(id__in=source_resource_ids)
        .values_list("id", flat=True)
    )
    if source_resource_ids.difference(accessible_resource_ids):
        raise PermissionError("Map layer source is not accessible")

    pinned_ids = {
        layer.dataset_version_id
        for layer in parsed.layers
        if layer.binding == MapLayerBindingMode.PINNED
        and layer.dataset_version_id is not None
    }
    pinned_versions = {
        version.id: version
        for version in DatasetVersion.objects.filter(id__in=pinned_ids)
    }
    if pinned_ids.difference(pinned_versions):
        raise ValueError("One or more pinned dataset versions do not exist")

    resolved_versions: dict[str, DatasetVersion] = {}
    vector_requirements: set[tuple[UUID, UUID, str]] = set()
    raster_requirements: set[tuple[UUID, UUID]] = set()

    for layer in parsed.layers:
        dataset = datasets[layer.dataset_id]
        if dataset.status != DatasetStatus.READY:
            raise ValueError(f"Dataset {dataset.id} is not ready")

        expected_kind = DatasetKind.VECTOR if layer.kind == "VECTOR" else DatasetKind.RASTER
        if dataset.kind != expected_kind:
            raise ValueError(f"Layer {layer.id} kind does not match its dataset")

        if layer.binding == MapLayerBindingMode.CURRENT:
            source_version = dataset.current_version
            if source_version is None:
                raise ValueError(f"Dataset {dataset.id} has no active version")
        else:
            source_version = pinned_versions[layer.dataset_version_id]
            if source_version.dataset_id != dataset.id:
                raise ValueError(
                    f"Layer {layer.id} references a version from another dataset"
                )
        if source_version.status != DatasetVersionStatus.READY:
            raise ValueError(
                f"Layer {layer.id} references a dataset version that is not ready"
            )

        resolved_versions[layer.id] = source_version
        if layer.kind == "VECTOR":
            vector_requirements.add(
                (
                    dataset.id,
                    source_version.id,
                    layer.source_layer_name or "",
                )
            )
        else:
            raster_requirements.add((dataset.id, source_version.id))

    vector_available = set(
        VectorLayer.objects.filter(
            vector_dataset__dataset_id__in={item[0] for item in vector_requirements},
            version_id__in={item[1] for item in vector_requirements},
            source_layer_name__in={item[2] for item in vector_requirements},
            status=VectorLayerStatus.READY,
        ).values_list(
            "vector_dataset__dataset_id",
            "version_id",
            "source_layer_name",
        )
    )
    raster_available = set(
        RasterPublication.objects.filter(
            raster_dataset__dataset_id__in={item[0] for item in raster_requirements},
            version_id__in={item[1] for item in raster_requirements},
            status=RasterPublicationStatus.READY,
        ).values_list("raster_dataset__dataset_id", "version_id")
    )

    for layer in parsed.layers:
        source_version = resolved_versions[layer.id]
        if layer.kind == "VECTOR":
            requirement = (
                layer.dataset_id,
                source_version.id,
                layer.source_layer_name or "",
            )
            if requirement not in vector_available:
                raise ValueError(
                    f"Layer {layer.id} does not resolve to a ready vector layer"
                )
        elif (layer.dataset_id, source_version.id) not in raster_available:
            raise ValueError(
                f"Layer {layer.id} does not resolve to a ready raster publication"
            )

    return resolved_versions


def validate_map_version_sources(
    *,
    actor: User,
    version: MapDocumentVersion,
) -> None:
    """Recheck every source before disclosing or activating a map snapshot."""

    parsed, _canonical = validate_map_document(version.document)
    _validate_source_references(actor=actor, parsed=parsed)


def _create_version(
    *,
    map_document: MapDocument,
    actor: User,
    parsed: MapDocumentSchema,
    canonical: dict[str, Any],
    resolved_versions: dict[str, DatasetVersion],
    note: str,
) -> MapDocumentVersion:
    maximum = map_document.versions.aggregate(value=Max("version_number"))["value"] or 0
    version = MapDocumentVersion.objects.create(
        map_document=map_document,
        version_number=maximum + 1,
        schema_version=parsed.schema_version,
        document=canonical,
        checksum_sha256=_document_checksum(canonical),
        created_by=actor,
        note=note,
    )
    references = []
    for ordinal, layer in enumerate(parsed.layers):
        references.append(
            MapLayerReference(
                version=version,
                ordinal=ordinal,
                client_layer_id=layer.id,
                title=layer.title,
                kind=layer.kind,
                binding_mode=layer.binding,
                dataset_id=layer.dataset_id,
                dataset_version=(
                    resolved_versions[layer.id]
                    if layer.binding == MapLayerBindingMode.PINNED
                    else None
                ),
                source_layer_name=layer.source_layer_name or "",
                visible=layer.visible,
                opacity=layer.opacity,
                min_zoom=layer.min_zoom,
                max_zoom=layer.max_zoom,
                style=layer.style,
                filter=layer.filter,
                popup=layer.popup,
                legend=layer.legend,
            )
        )
    MapLayerReference.objects.bulk_create(references)
    return version


def _activate_version(
    *,
    map_document: MapDocument,
    version: MapDocumentVersion,
    actor: User,
    action: str,
    note: str,
) -> None:
    if version.map_document_id != map_document.id:
        raise ValueError("Map version does not belong to this map")

    now = timezone.now()
    previous = (
        MapDocumentVersion.objects.filter(pk=map_document.current_version_id).first()
        if map_document.current_version_id
        else None
    )
    if previous is not None and previous.id != version.id:
        previous.deactivated_at = now
        previous.save(update_fields=("deactivated_at",))

    version.activated_at = now
    version.deactivated_at = None
    version.activation_count += 1
    version.save(
        update_fields=("activated_at", "deactivated_at", "activation_count"),
    )
    map_document.current_version = version
    map_document.save(update_fields=("current_version", "updated_at"))

    resource = Resource.objects.get(pk=map_document.resource_id)
    if resource.lifecycle_status in {
        LifecycleStatus.DRAFT,
        LifecycleStatus.PROCESSING,
        LifecycleStatus.FAILED,
    }:
        resource.lifecycle_status = LifecycleStatus.READY
        resource.save(update_fields=("lifecycle_status", "updated_at"))

    MapDocumentVersionActivation.objects.create(
        map_document=map_document,
        from_version=previous,
        to_version=version,
        action=action,
        activated_by=actor,
        note=note,
    )


def _locked_editable_map(*, actor: User, map_document_id: UUID) -> MapDocument:
    map_document = MapDocument.objects.select_for_update().filter(
        pk=map_document_id,
    ).first()
    if map_document is None:
        raise ValueError("Map document not found")
    resource = Resource.objects.get(pk=map_document.resource_id)
    if not has_resource_permission(actor, resource, PermissionAction.EDIT):
        raise PermissionError("Map document not found")
    if resource.lifecycle_status == LifecycleStatus.ARCHIVED:
        raise ValueError("Archived map documents cannot be modified")
    return map_document


@transaction.atomic
def create_map_document(
    *,
    actor: User,
    title: str,
    slug: str,
    document: dict[str, Any],
    description: str = "",
    visibility: str = Visibility.PRIVATE,
    organization: Organization | None = None,
    note: str = "",
) -> MapDocumentWriteResult:
    parsed, canonical = validate_map_document(document)
    resolved_versions = _validate_source_references(actor=actor, parsed=parsed)
    normalized_note = _validate_note(note)

    resource = create_resource(
        owner=actor,
        resource_type=ResourceType.MAP,
        title=title,
        slug=slug,
        description=description,
        visibility=visibility,
        organization=organization,
    )
    map_document = MapDocument.objects.create(resource=resource)
    version = _create_version(
        map_document=map_document,
        actor=actor,
        parsed=parsed,
        canonical=canonical,
        resolved_versions=resolved_versions,
        note=normalized_note,
    )
    _activate_version(
        map_document=map_document,
        version=version,
        actor=actor,
        action=MapVersionActivationAction.INITIAL,
        note=normalized_note,
    )
    return MapDocumentWriteResult(map_document=map_document, version=version)


@transaction.atomic
def create_map_document_version(
    *,
    actor: User,
    map_document_id: UUID,
    document: dict[str, Any],
    note: str = "",
    activate: bool = True,
) -> MapDocumentWriteResult:
    map_document = _locked_editable_map(
        actor=actor,
        map_document_id=map_document_id,
    )
    parsed, canonical = validate_map_document(document)
    resolved_versions = _validate_source_references(actor=actor, parsed=parsed)
    normalized_note = _validate_note(note)
    version = _create_version(
        map_document=map_document,
        actor=actor,
        parsed=parsed,
        canonical=canonical,
        resolved_versions=resolved_versions,
        note=normalized_note,
    )
    if activate:
        _activate_version(
            map_document=map_document,
            version=version,
            actor=actor,
            action=MapVersionActivationAction.SAVE,
            note=normalized_note,
        )
    return MapDocumentWriteResult(map_document=map_document, version=version)


@transaction.atomic
def activate_map_document_version(
    *,
    actor: User,
    map_document_id: UUID,
    version_id: UUID,
    note: str = "",
) -> MapDocumentVersion:
    map_document = _locked_editable_map(
        actor=actor,
        map_document_id=map_document_id,
    )
    version = MapDocumentVersion.objects.filter(
        pk=version_id,
        map_document=map_document,
    ).first()
    if version is None:
        raise ValueError("Map version not found")

    validate_map_version_sources(actor=actor, version=version)
    normalized_note = _validate_note(note)
    current = (
        MapDocumentVersion.objects.filter(pk=map_document.current_version_id).first()
        if map_document.current_version_id
        else None
    )
    action = MapVersionActivationAction.MANUAL
    if current is not None and version.version_number < current.version_number:
        action = MapVersionActivationAction.ROLLBACK
    _activate_version(
        map_document=map_document,
        version=version,
        actor=actor,
        action=action,
        note=normalized_note,
    )
    return version
