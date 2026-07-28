from importlib import import_module

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.jobs"
    label = "jobs"

    def ready(self) -> None:
        import_module("modules.jobs.handlers")
        import_module("modules.dataset_inspection.handlers")
        import_module("modules.datasets.handlers")
        import_module("modules.vector_exports.handlers")
