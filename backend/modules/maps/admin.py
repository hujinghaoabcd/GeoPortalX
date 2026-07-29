from django.contrib import admin

from .models import (
    MapDocument,
    MapDocumentVersion,
    MapDocumentVersionActivation,
    MapLayerReference,
)


class ServiceManagedAdmin(admin.ModelAdmin):
    """Expose service-managed records for inspection without bypassing invariants."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None) -> tuple[str, ...]:
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(MapDocument)
class MapDocumentAdmin(ServiceManagedAdmin):
    list_display = ("id", "resource", "current_version", "updated_at")
    search_fields = ("resource__title", "resource__slug")
    list_select_related = ("resource", "current_version")


@admin.register(MapDocumentVersion)
class MapDocumentVersionAdmin(ServiceManagedAdmin):
    list_display = (
        "id",
        "map_document",
        "version_number",
        "schema_version",
        "created_by",
        "created_at",
    )
    search_fields = ("map_document__resource__title", "checksum_sha256")
    list_select_related = ("map_document", "map_document__resource", "created_by")


@admin.register(MapLayerReference)
class MapLayerReferenceAdmin(ServiceManagedAdmin):
    list_display = (
        "id",
        "version",
        "ordinal",
        "client_layer_id",
        "kind",
        "binding_mode",
        "dataset",
    )
    search_fields = ("client_layer_id", "title", "source_layer_name")
    list_filter = ("kind", "binding_mode", "visible")
    list_select_related = ("version", "dataset", "dataset_version")


@admin.register(MapDocumentVersionActivation)
class MapDocumentVersionActivationAdmin(ServiceManagedAdmin):
    list_display = (
        "id",
        "map_document",
        "from_version",
        "to_version",
        "action",
        "activated_by",
        "created_at",
    )
    list_filter = ("action",)
    list_select_related = (
        "map_document",
        "from_version",
        "to_version",
        "activated_by",
    )
