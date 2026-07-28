import uuid

from django.conf import settings
from django.contrib.gis.db import models


class DatasetKind(models.TextChoices):
    VECTOR = "VECTOR", "Vector"
    RASTER = "RASTER", "Raster"


class DatasetStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    IMPORTING = "IMPORTING", "Importing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"
    ARCHIVED = "ARCHIVED", "Archived"


class DatasetVersionStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    IMPORTING = "IMPORTING", "Importing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class VectorLayerStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    IMPORTING = "IMPORTING", "Importing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class Dataset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.OneToOneField(
        "resources.Resource",
        on_delete=models.CASCADE,
        related_name="dataset",
    )
    kind = models.CharField(max_length=16, choices=DatasetKind.choices, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=DatasetStatus.choices,
        default=DatasetStatus.REGISTERED,
        db_index=True,
    )
    current_version = models.ForeignKey(
        "DatasetVersion",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.resource.title


class DatasetVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    source_upload = models.OneToOneField(
        "uploads.UploadSession",
        on_delete=models.PROTECT,
        related_name="dataset_version",
    )
    inspection_job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.PROTECT,
        related_name="registered_dataset_version",
    )
    status = models.CharField(
        max_length=16,
        choices=DatasetVersionStatus.choices,
        default=DatasetVersionStatus.REGISTERED,
        db_index=True,
    )
    source_format = models.CharField(max_length=64)
    source_checksum_sha256 = models.CharField(max_length=64)
    inspection_result = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dataset_versions",
    )
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("dataset", "version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("dataset", "version_number"),
                name="dataset_version_number_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dataset.resource.title} v{self.version_number}"


class VectorDataset(models.Model):
    dataset = models.OneToOneField(
        Dataset,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="vector",
    )
    layer_count = models.PositiveIntegerField(default=0)
    imported_layer_count = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return self.dataset.resource.title


class VectorLayer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vector_dataset = models.ForeignKey(
        VectorDataset,
        on_delete=models.CASCADE,
        related_name="layers",
    )
    version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="vector_layers",
    )
    ordinal = models.PositiveIntegerField()
    source_layer_name = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16,
        choices=VectorLayerStatus.choices,
        default=VectorLayerStatus.REGISTERED,
        db_index=True,
    )
    source_driver = models.CharField(max_length=64, blank=True)
    source_crs = models.TextField(blank=True)
    source_bounds = models.JSONField(default=list, blank=True)
    field_schema = models.JSONField(default=list, blank=True)
    field_statistics = models.JSONField(default=list, blank=True)
    quality_report = models.JSONField(default=dict, blank=True)
    geometry_type = models.CharField(max_length=64, blank=True)
    geometry_column = models.CharField(max_length=63, default="geom")
    srid = models.IntegerField(null=True, blank=True)
    feature_count = models.BigIntegerField(default=0)
    db_schema = models.CharField(max_length=63, blank=True)
    db_table = models.CharField(max_length=63, blank=True)
    tile_source_id = models.CharField(max_length=128, blank=True, db_index=True)
    min_zoom = models.PositiveSmallIntegerField(default=0)
    max_zoom = models.PositiveSmallIntegerField(default=14)
    extent = models.PolygonField(srid=4326, null=True, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("ordinal", "source_layer_name")
        constraints = [
            models.UniqueConstraint(
                fields=("version", "source_layer_name"),
                name="vector_version_source_layer_unique",
            ),
            models.UniqueConstraint(
                fields=("db_schema", "db_table"),
                condition=~models.Q(db_schema="") & ~models.Q(db_table=""),
                name="vector_layer_storage_unique",
            ),
            models.UniqueConstraint(
                fields=("tile_source_id",),
                condition=~models.Q(tile_source_id=""),
                name="vector_layer_tile_source_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(min_zoom__lte=models.F("max_zoom")),
                name="vector_layer_zoom_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vector_dataset.dataset.resource.title}: {self.title}"


class RasterDataset(models.Model):
    dataset = models.OneToOneField(
        Dataset,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="raster",
    )
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    band_count = models.PositiveIntegerField()
    driver = models.CharField(max_length=64)
    crs = models.TextField(blank=True)
    epsg = models.IntegerField(null=True, blank=True)
    source_bounds = models.JSONField(default=list, blank=True)
    transform = models.JSONField(default=list, blank=True)
    bands = models.JSONField(default=list, blank=True)
    image_structure = models.JSONField(default=dict, blank=True)
    cog_readiness = models.JSONField(default=dict, blank=True)
    published_bucket = models.CharField(max_length=255, blank=True)
    published_key = models.CharField(max_length=1024, blank=True)

    def __str__(self) -> str:
        return self.dataset.resource.title
