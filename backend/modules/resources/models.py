import uuid

from django.conf import settings
from django.contrib.gis.db import models


class ResourceType(models.TextChoices):
    VECTOR_DATASET = "VECTOR_DATASET", "Vector dataset"
    RASTER_DATASET = "RASTER_DATASET", "Raster dataset"
    TABLE_DATASET = "TABLE_DATASET", "Table dataset"
    MAP = "MAP", "Map"
    SCENE = "SCENE", "Scene"
    DASHBOARD = "DASHBOARD", "Dashboard"
    STORY = "STORY", "Story"
    DOCUMENT = "DOCUMENT", "Document"
    SERVICE = "SERVICE", "Service"
    APPLICATION = "APPLICATION", "Application"
    ANALYSIS_RESULT = "ANALYSIS_RESULT", "Analysis result"


class Visibility(models.TextChoices):
    PRIVATE = "PRIVATE", "Private"
    ORGANIZATION = "ORGANIZATION", "Organization"
    AUTHENTICATED = "AUTHENTICATED", "Authenticated"
    PUBLIC = "PUBLIC", "Public"


class LifecycleStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PROCESSING = "PROCESSING", "Processing"
    READY = "READY", "Ready"
    PUBLISHED = "PUBLISHED", "Published"
    FAILED = "FAILED", "Failed"
    ARCHIVED = "ARCHIVED", "Archived"


class Resource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource_type = models.CharField(max_length=32, choices=ResourceType.choices, db_index=True)
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="resources")
    visibility = models.CharField(max_length=24, choices=Visibility.choices, default=Visibility.PRIVATE, db_index=True)
    lifecycle_status = models.CharField(max_length=24, choices=LifecycleStatus.choices, default=LifecycleStatus.DRAFT, db_index=True)
    spatial_extent = models.PolygonField(srid=4326, null=True, blank=True)
    temporal_start = models.DateTimeField(null=True, blank=True)
    temporal_end = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(fields=("owner", "slug"), name="resource_owner_slug_unique"),
        ]

    def __str__(self) -> str:
        return self.title
