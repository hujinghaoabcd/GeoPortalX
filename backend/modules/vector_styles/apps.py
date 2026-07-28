from importlib import import_module

from django.apps import AppConfig


class VectorStylesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.vector_styles"
    label = "vector_styles"

    def ready(self) -> None:
        import_module("modules.vector_styles.signals")
