import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("datasets", "0002_vector_quality_and_tiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="datasetversion",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="datasetversion",
            name="activation_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="datasetversion",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DatasetVersionActivation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("INITIAL", "Initial activation"),
                            ("REPLACEMENT", "Replacement activation"),
                            ("ROLLBACK", "Rollback"),
                            ("MANUAL", "Manual activation"),
                        ],
                        max_length=16,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "activated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dataset_version_activations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="version_activations",
                        to="datasets.dataset",
                    ),
                ),
                (
                    "from_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activation_departures",
                        to="datasets.datasetversion",
                    ),
                ),
                (
                    "to_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activation_arrivals",
                        to="datasets.datasetversion",
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
    ]
