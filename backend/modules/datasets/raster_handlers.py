from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from modules.dataset_inspection.materialization import materialize_completed_upload
from modules.jobs.context import JobExecutionContext
from modules.jobs.models import Job
from modules.jobs.registry import register_job_handler
from modules.jobs.services import JobCancellationRequested
from modules.object_storage.publication import publish_file
from modules.object_storage.services import delete_object
from modules.permissions.models import PermissionAction
from modules.permissions.services import has_resource_permission

from .models import (
    RasterPublication,
    RasterPublicationStatus,
    RasterRenderMode,
    RasterRenderSettings,
)

if TYPE_CHECKING:
    from .raster_conversion import RasterCogMetadata


@register_job_handler("raster-publish")
def run_raster_publish(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    publication, job = _resolve_publication(context, parameters)
    if publication.status == RasterPublicationStatus.READY:
        return _result_payload(publication)

    object_key = _object_key(publication)
    try:
        _mark_processing(publication)
        context.report_progress(10, "Validated raster publication")
        with materialize_completed_upload(publication.version.source_upload) as materialized:
            context.report_progress(25, "Materialized raster source")
            destination = Path(materialized.path.parent) / "published.cog.tif"
            from .raster_conversion import convert_to_cog

            metadata = convert_to_cog(source=materialized.path, destination=destination)
            context.ensure_not_cancelled()
            context.report_progress(70, "Generated and validated Cloud Optimized GeoTIFF")
            published = publish_file(
                path=destination,
                key=object_key,
                content_type="image/tiff; application=geotiff; profile=cloud-optimized",
                metadata={
                    "geoportalx-publication-id": str(publication.id),
                    "geoportalx-dataset-id": str(publication.raster_dataset_id),
                    "geoportalx-version-id": str(publication.version_id),
                },
            )
            context.report_progress(90, "Published COG to object storage")
            _mark_ready(publication, metadata=metadata, published=published, actor=job.created_by)
    except JobCancellationRequested:
        with suppress(Exception):
            delete_object(key=object_key)
        _mark_cancelled(publication)
        raise
    except Exception as exc:
        with suppress(Exception):
            delete_object(key=object_key)
        _mark_failed(publication, exc)
        raise
    return _result_payload(publication)


def _resolve_publication(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> tuple[RasterPublication, Job]:
    raw_id = parameters.get("raster_publication_id")
    try:
        publication_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("raster_publication_id must be a UUID") from exc
    try:
        publication = RasterPublication.objects.select_related(
            "raster_dataset",
            "raster_dataset__dataset",
            "raster_dataset__dataset__resource",
            "raster_dataset__dataset__current_version",
            "version",
            "version__source_upload",
        ).get(pk=publication_id)
    except RasterPublication.DoesNotExist as exc:
        raise ValueError("Raster publication does not exist") from exc

    dataset = publication.raster_dataset.dataset
    if publication.version_id != dataset.current_version_id:
        raise ValueError("Only the active raster version can be published")
    job = Job.objects.select_related("created_by", "resource").get(pk=context.job_id)
    if job.resource_id != dataset.resource_id:
        raise PermissionError("Publication job resource does not match the dataset")
    if not has_resource_permission(job.created_by, dataset.resource, PermissionAction.EDIT):
        raise PermissionError("Publication job creator cannot edit the dataset")
    if (
        publication.version.source_upload.created_by_id != job.created_by_id
        and not job.created_by.is_superuser
    ):
        raise PermissionError("Publication job creator does not own the source upload")
    return publication, job


@transaction.atomic
def _mark_processing(publication: RasterPublication) -> None:
    locked = RasterPublication.objects.select_for_update().get(pk=publication.pk)
    locked.status = RasterPublicationStatus.PROCESSING
    locked.started_at = locked.started_at or timezone.now()
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save(
        update_fields=(
            "status",
            "started_at",
            "failure_code",
            "failure_message",
            "updated_at",
        )
    )


@transaction.atomic
def _mark_ready(
    publication,
    *,
    metadata: "RasterCogMetadata",
    published,
    actor,
) -> None:
    locked = RasterPublication.objects.select_for_update().select_related(
        "raster_dataset",
        "raster_dataset__dataset",
    ).get(pk=publication.pk)
    locked.status = RasterPublicationStatus.READY
    locked.bucket = published.bucket
    locked.object_key = published.key
    locked.object_version_id = published.version_id
    locked.object_etag = published.etag
    locked.checksum_sha256 = published.checksum_sha256
    locked.content_type = published.content_type
    locked.object_size = published.size
    locked.width = metadata.width
    locked.height = metadata.height
    locked.band_count = metadata.band_count
    locked.crs = metadata.crs
    locked.epsg = metadata.epsg
    locked.bounds = metadata.bounds
    locked.transform = metadata.transform
    locked.bands = metadata.bands
    locked.statistics = metadata.statistics
    locked.image_structure = metadata.image_structure
    locked.cog_profile = metadata.cog_profile
    locked.min_zoom = metadata.min_zoom
    locked.max_zoom = metadata.max_zoom
    locked.completed_at = timezone.now()
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save()

    RasterRenderSettings.objects.update_or_create(
        publication=locked,
        defaults={**_default_render(metadata), "updated_by": actor},
    )
    dataset = locked.raster_dataset.dataset
    if dataset.current_version_id == locked.version_id:
        locked.raster_dataset.published_bucket = locked.bucket
        locked.raster_dataset.published_key = locked.object_key
        locked.raster_dataset.save(update_fields=("published_bucket", "published_key"))


@transaction.atomic
def _mark_cancelled(publication: RasterPublication) -> None:
    locked = RasterPublication.objects.select_for_update().get(pk=publication.pk)
    locked.status = RasterPublicationStatus.CANCELLED
    locked.failure_code = ""
    locked.failure_message = ""
    locked.save(update_fields=("status", "failure_code", "failure_message", "updated_at"))


@transaction.atomic
def _mark_failed(publication: RasterPublication, exc: Exception) -> None:
    locked = RasterPublication.objects.select_for_update().get(pk=publication.pk)
    locked.status = RasterPublicationStatus.FAILED
    locked.failure_code = exc.__class__.__name__.upper()[:100]
    locked.failure_message = str(exc)
    locked.save(
        update_fields=("status", "failure_code", "failure_message", "updated_at")
    )


def _object_key(publication: RasterPublication) -> str:
    resource_id = publication.raster_dataset.dataset.resource_id
    return (
        f"rasters/{resource_id}/{publication.version_id}/"
        f"{publication.id}.cog.tif"
    )


def _default_render(metadata: "RasterCogMetadata") -> dict[str, Any]:
    usable = [
        item
        for item in metadata.statistics
        if item.get("percentile_2") is not None and item.get("percentile_98") is not None
    ]
    if metadata.band_count >= 3 and len(usable) >= 3:
        selected = usable[:3]
        return {
            "mode": RasterRenderMode.RGB,
            "bands": [int(item["band"]) for item in selected],
            "rescale": [
                [float(item["percentile_2"]), float(item["percentile_98"])]
                for item in selected
            ],
            "colormap_name": "",
            "resampling": "bilinear",
            "opacity": 1.0,
            "revision": 1,
        }
    first = usable[0] if usable else {
        "band": 1,
        "percentile_2": 0.0,
        "percentile_98": 1.0,
    }
    low = float(first["percentile_2"])
    high = float(first["percentile_98"])
    if high <= low:
        high = low + 1.0
    return {
        "mode": RasterRenderMode.SINGLE_BAND,
        "bands": [int(first["band"])],
        "rescale": [[low, high]],
        "colormap_name": "viridis",
        "resampling": "bilinear",
        "opacity": 1.0,
        "revision": 1,
    }


def _result_payload(publication: RasterPublication) -> dict[str, Any]:
    publication.refresh_from_db()
    return {
        "raster_publication_id": str(publication.id),
        "dataset_id": str(publication.raster_dataset_id),
        "dataset_version_id": str(publication.version_id),
        "status": publication.status,
        "bucket": publication.bucket,
        "object_key": publication.object_key,
        "checksum_sha256": publication.checksum_sha256,
        "object_size": publication.object_size,
        "width": publication.width,
        "height": publication.height,
        "band_count": publication.band_count,
        "bounds": publication.bounds,
        "statistics": publication.statistics,
        "min_zoom": publication.min_zoom,
        "max_zoom": publication.max_zoom,
    }
