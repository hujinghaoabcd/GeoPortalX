from importlib import import_module

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.jobs"
    label = "jobs"

    def ready(self) -> None:
        import_module("modules.jobs.handlers")
