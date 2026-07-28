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


class DatasetVersionActivationAction(models.TextChoices):
    INITIAL = "INITIAL", "Initial activation"
    REPLACEMENT = "REPLACEMENT", "Replacement activation"
    ROLLBACK = "ROLLBACK", "Rollback"
    MANUAL = "MANUAL", "Manual activation"


class VectorLayerStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered"
    IMPORTING = "IMPORTING", "Importing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class RasterPublicationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class RasterRenderMode(models.TextChoices):
    SINGLE_BAND = "SINGLE_BAND", "Single band"
    RGB = "RGB", "RGB"


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
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    activation_count = models.PositiveIntegerField(default=0)

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


class DatasetVersionActivation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="version_activations",
    )
    from_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.PROTECT,
        related_name="activation_departures",
        null=True,
        blank=True,
    )
    to_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.PROTECT,
        related_name="activation_arrivals",
    )
    action = models.CharField(
        max_length=16,
        choices=DatasetVersionActivationAction.choices,
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dataset_version_activations",
    )
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.dataset}: {self.action} -> v{self.to_version.version_number}"


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


class RasterPublication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raster_dataset = models.ForeignKey(
        RasterDataset,
        on_delete=models.CASCADE,
        related_name="publications",
    )
    version = models.OneToOneField(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="raster_publication",
    )
    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.PROTECT,
        related_name="raster_publication",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=RasterPublicationStatus.choices,
        default=RasterPublicationStatus.PENDING,
        db_index=True,
    )
    bucket = models.CharField(max_length=255, blank=True)
    object_key = models.CharField(max_length=1024, blank=True)
    object_version_id = models.CharField(max_length=255, blank=True)
    object_etag = models.CharField(max_length=255, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    object_size = models.PositiveBigIntegerField(default=0)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    band_count = models.PositiveIntegerField(default=0)
    crs = models.TextField(blank=True)
    epsg = models.IntegerField(null=True, blank=True)
    bounds = models.JSONField(default=list, blank=True)
    transform = models.JSONField(default=list, blank=True)
    bands = models.JSONField(default=list, blank=True)
    statistics = models.JSONField(default=list, blank=True)
    image_structure = models.JSONField(default=dict, blank=True)
    cog_profile = models.JSONField(default=dict, blank=True)
    min_zoom = models.PositiveSmallIntegerField(default=0)
    max_zoom = models.PositiveSmallIntegerField(default=22)
    failure_code = models.CharField(max_length=100, blank=True)
    failure_message = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="raster_publications",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_zoom__lte=models.F("max_zoom")),
                name="raster_publication_zoom_order",
            ),
        ]
        indexes = [
            models.Index(
                fields=("raster_dataset", "status"),
                name="raster_pub_dataset_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.raster_dataset}: v{self.version.version_number} ({self.status})"


class RasterRenderSettings(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.OneToOneField(
        RasterPublication,
        on_delete=models.CASCADE,
        related_name="render_settings",
    )
    mode = models.CharField(
        max_length=16,
        choices=RasterRenderMode.choices,
        default=RasterRenderMode.SINGLE_BAND,
    )
    bands = models.JSONField(default=list)
    rescale = models.JSONField(default=list)
    colormap_name = models.CharField(max_length=64, blank=True)
    resampling = models.CharField(max_length=32, default="bilinear")
    opacity = models.FloatField(default=1.0)
    revision = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="updated_raster_render_settings",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(opacity__gte=0.0, opacity__lte=1.0),
                name="raster_render_opacity_range",
            ),
        ]

    def __str__(self) -> str:
        return f"Render settings for {self.publication}"
