from importlib import import_module

from django.apps import AppConfig


class DatasetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.datasets"
    label = "datasets"

    def ready(self) -> None:
        import_module("modules.datasets.signals")
        import_module("modules.datasets.raster_handlers")
