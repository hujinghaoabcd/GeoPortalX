import uuid

from django.conf import settings
from django.db import models


class VectorStyleMode(models.TextChoices):
    SIMPLE = "SIMPLE", "Simple symbol"
    CATEGORICAL = "CATEGORICAL", "Categorical"
    GRADUATED = "GRADUATED", "Graduated"


class VectorStyleClassificationMethod(models.TextChoices):
    UNIQUE_VALUES = "UNIQUE_VALUES", "Unique values"
    EQUAL_INTERVAL = "EQUAL_INTERVAL", "Equal interval"


class VectorStyle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer = models.OneToOneField(
        "datasets.VectorLayer",
        on_delete=models.CASCADE,
        related_name="default_style",
    )
    mode = models.CharField(
        max_length=16,
        choices=VectorStyleMode.choices,
        default=VectorStyleMode.SIMPLE,
        db_index=True,
    )
    field_name = models.CharField(max_length=63, blank=True)
    classification_method = models.CharField(
        max_length=24,
        choices=VectorStyleClassificationMethod.choices,
        blank=True,
    )
    class_count = models.PositiveSmallIntegerField(default=5)
    palette = models.CharField(max_length=32, default="BLUES")
    symbol = models.JSONField(default=dict, blank=True)
    classes = models.JSONField(default=list, blank=True)
    fallback_symbol = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="vector_styles_updated",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("layer_id",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(class_count__gte=1, class_count__lte=12),
                name="vector_style_class_count_range",
            )
        ]

    def __str__(self) -> str:
        return f"{self.layer}: {self.mode}"
