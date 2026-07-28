import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_ready_layer_styles(apps, schema_editor):
    VectorLayer = apps.get_model("datasets", "VectorLayer")
    VectorStyle = apps.get_model("vector_styles", "VectorStyle")
    styles = []
    for layer in VectorLayer.objects.filter(status="READY").iterator():
        geometry_type = str(layer.geometry_type or "").upper()
        if "POINT" in geometry_type:
            symbol = {
                "color": "#2563eb",
                "opacity": 0.85,
                "size": 6,
                "outline_color": "#ffffff",
                "outline_width": 1,
            }
        elif "POLYGON" in geometry_type:
            symbol = {
                "color": "#3b82f6",
                "opacity": 0.45,
                "outline_color": "#1d4ed8",
                "outline_width": 1,
            }
        else:
            symbol = {"color": "#2563eb", "opacity": 0.9, "width": 2.5}
        styles.append(
            VectorStyle(
                id=uuid.uuid4(),
                layer_id=layer.id,
                mode="SIMPLE",
                class_count=5,
                palette="BLUES",
                symbol=symbol,
                fallback_symbol={"color": "#9ca3af", "opacity": 0.65},
            )
        )
    VectorStyle.objects.bulk_create(styles, ignore_conflicts=True)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("datasets", "0002_vector_quality_and_tiles"),
    ]

    operations = [
        migrations.CreateModel(
            name="VectorStyle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("mode", models.CharField(choices=[("SIMPLE", "Simple symbol"), ("CATEGORICAL", "Categorical"), ("GRADUATED", "Graduated")], db_index=True, default="SIMPLE", max_length=16)),
                ("field_name", models.CharField(blank=True, max_length=63)),
                ("classification_method", models.CharField(blank=True, choices=[("UNIQUE_VALUES", "Unique values"), ("EQUAL_INTERVAL", "Equal interval")], max_length=24)),
                ("class_count", models.PositiveSmallIntegerField(default=5)),
                ("palette", models.CharField(default="BLUES", max_length=32)),
                ("symbol", models.JSONField(blank=True, default=dict)),
                ("classes", models.JSONField(blank=True, default=list)),
                ("fallback_symbol", models.JSONField(blank=True, default=dict)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("layer", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="default_style", to="datasets.vectorlayer")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vector_styles_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("layer_id",)},
        ),
        migrations.AddConstraint(
            model_name="vectorstyle",
            constraint=models.CheckConstraint(condition=models.Q(("class_count__gte", 1), ("class_count__lte", 12)), name="vector_style_class_count_range"),
        ),
        migrations.RunPython(create_ready_layer_styles, migrations.RunPython.noop),
    ]
