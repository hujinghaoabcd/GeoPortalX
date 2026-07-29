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
from modules.resources.services import create_resource

from .document_schema import MapDocumentSchema, MapLayerDocument, validate_map_document
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
    missing = dataset_ids.difference(datasets)
    if missing:
        raise ValueError("One or more referenced datasets do not exist")

    resolved_versions: dict[str, DatasetVersion] = {}
    for layer in parsed.layers:
        dataset = datasets[layer.dataset_id]
        if not has_resource_permission(actor, dataset.resource, PermissionAction.VIEW):
            raise PermissionError("Map layer source is not accessible")
        if dataset.status != DatasetStatus.READY:
            raise ValueError(f"Dataset {dataset.id} is not ready")

        expected_kind = DatasetKind.VECTOR if layer.kind == "VECTOR" else DatasetKind.RASTER
        if dataset.kind != expected_kind:
            raise ValueError(f"Layer {layer.id} kind does not match its dataset")

        source_version = _resolve_source_version(dataset=dataset, layer=layer)
        if source_version.status != DatasetVersionStatus.READY:
            raise ValueError(f"Layer {layer.id} references a dataset version that is not ready")

        if layer.kind == "VECTOR":
            available = VectorLayer.objects.filter(
                vector_dataset__dataset=dataset,
                version=source_version,
                source_layer_name=layer.source_layer_name,
                status=VectorLayerStatus.READY,
            ).exists()
            if not available:
                raise ValueError(
                    f"Layer {layer.id} does not resolve to a ready vector layer"
                )
        else:
            available = RasterPublication.objects.filter(
                raster_dataset__dataset=dataset,
                version=source_version,
                status=RasterPublicationStatus.READY,
            ).exists()
            if not available:
                raise ValueError(
                    f"Layer {layer.id} does not resolve to a ready raster publication"
                )
        resolved_versions[layer.id] = source_version

    return resolved_versions


def _resolve_source_version(
    *,
    dataset: Dataset,
    layer: MapLayerDocument,
) -> DatasetVersion:
    if layer.binding == MapLayerBindingMode.CURRENT:
        if dataset.current_version is None:
            raise ValueError(f"Dataset {dataset.id} has no active version")
        return dataset.current_version

    version = DatasetVersion.objects.filter(
        pk=layer.dataset_version_id,
        dataset=dataset,
    ).first()
    if version is None:
        raise ValueError(f"Layer {layer.id} references an unknown dataset version")
    return version


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
    if resource.lifecycle_status != LifecycleStatus.READY:
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
    map_document = MapDocument.objects.select_for_update().filter(
        pk=map_document_id,
    ).first()
    if map_document is None:
        raise ValueError("Map document not found")
    resource = Resource.objects.get(pk=map_document.resource_id)
    if not has_resource_permission(actor, resource, PermissionAction.EDIT):
        raise PermissionError("Map document not found")

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
    map_document = MapDocument.objects.select_for_update().filter(
        pk=map_document_id,
    ).first()
    if map_document is None:
        raise ValueError("Map document not found")
    resource = Resource.objects.get(pk=map_document.resource_id)
    if not has_resource_permission(actor, resource, PermissionAction.EDIT):
        raise PermissionError("Map document not found")

    version = MapDocumentVersion.objects.filter(
        pk=version_id,
        map_document=map_document,
    ).first()
    if version is None:
        raise ValueError("Map version not found")

    parsed, _canonical = validate_map_document(version.document)
    _validate_source_references(actor=actor, parsed=parsed)
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
