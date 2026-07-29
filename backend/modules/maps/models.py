import uuid

from django.conf import settings
from django.contrib.gis.db import models


class MapLayerKind(models.TextChoices):
    VECTOR = "VECTOR", "Vector"
    RASTER = "RASTER", "Raster"


class MapLayerBindingMode(models.TextChoices):
    CURRENT = "CURRENT", "Current dataset version"
    PINNED = "PINNED", "Pinned dataset version"


class MapVersionActivationAction(models.TextChoices):
    INITIAL = "INITIAL", "Initial activation"
    SAVE = "SAVE", "Save and activate"
    ROLLBACK = "ROLLBACK", "Rollback"
    MANUAL = "MANUAL", "Manual activation"


class MapDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.OneToOneField(
        "resources.Resource",
        on_delete=models.CASCADE,
        related_name="map_document",
    )
    current_version = models.ForeignKey(
        "MapDocumentVersion",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.resource.title


class MapDocumentVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    map_document = models.ForeignKey(
        MapDocument,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    schema_version = models.PositiveSmallIntegerField(default=1)
    document = models.JSONField(default=dict)
    checksum_sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="map_document_versions",
    )
    note = models.CharField(max_length=500, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    activation_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("map_document", "version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("map_document", "version_number"),
                name="map_document_version_number_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.map_document.resource.title} v{self.version_number}"


class MapDocumentVersionActivation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    map_document = models.ForeignKey(
        MapDocument,
        on_delete=models.CASCADE,
        related_name="version_activations",
    )
    from_version = models.ForeignKey(
        MapDocumentVersion,
        on_delete=models.PROTECT,
        related_name="activation_departures",
        null=True,
        blank=True,
    )
    to_version = models.ForeignKey(
        MapDocumentVersion,
        on_delete=models.PROTECT,
        related_name="activation_arrivals",
    )
    action = models.CharField(max_length=16, choices=MapVersionActivationAction.choices)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="map_document_version_activations",
    )
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.map_document}: {self.action} -> v{self.to_version.version_number}"


class MapLayerReference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        MapDocumentVersion,
        on_delete=models.CASCADE,
        related_name="layer_references",
    )
    ordinal = models.PositiveIntegerField()
    client_layer_id = models.CharField(max_length=128)
    title = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=MapLayerKind.choices)
    binding_mode = models.CharField(
        max_length=16,
        choices=MapLayerBindingMode.choices,
        default=MapLayerBindingMode.CURRENT,
    )
    dataset = models.ForeignKey(
        "datasets.Dataset",
        on_delete=models.PROTECT,
        related_name="map_layer_references",
    )
    dataset_version = models.ForeignKey(
        "datasets.DatasetVersion",
        on_delete=models.PROTECT,
        related_name="map_layer_references",
        null=True,
        blank=True,
    )
    source_layer_name = models.CharField(max_length=255, blank=True)
    visible = models.BooleanField(default=True)
    opacity = models.FloatField(default=1.0)
    min_zoom = models.FloatField(null=True, blank=True)
    max_zoom = models.FloatField(null=True, blank=True)
    style = models.JSONField(default=dict, blank=True)
    filter = models.JSONField(null=True, blank=True)
    popup = models.JSONField(default=dict, blank=True)
    legend = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("ordinal",)
        constraints = [
            models.UniqueConstraint(
                fields=("version", "ordinal"),
                name="map_layer_version_ordinal_unique",
            ),
            models.UniqueConstraint(
                fields=("version", "client_layer_id"),
                name="map_layer_version_client_id_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(opacity__gte=0.0, opacity__lte=1.0),
                name="map_layer_opacity_range",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(min_zoom__isnull=True)
                    | models.Q(max_zoom__isnull=True)
                    | models.Q(min_zoom__lte=models.F("max_zoom"))
                ),
                name="map_layer_zoom_order",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        binding_mode=MapLayerBindingMode.CURRENT,
                        dataset_version__isnull=True,
                    )
                    | models.Q(
                        binding_mode=MapLayerBindingMode.PINNED,
                        dataset_version__isnull=False,
                    )
                ),
                name="map_layer_binding_version_consistency",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind=MapLayerKind.VECTOR) & ~models.Q(source_layer_name="")
                )
                | models.Q(kind=MapLayerKind.RASTER, source_layer_name=""),
                name="map_layer_source_name_by_kind",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.version}: {self.title}"
