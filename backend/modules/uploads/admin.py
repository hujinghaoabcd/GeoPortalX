from django.contrib import admin

from .models import UploadSession


@admin.register(UploadSession)
class UploadSessionAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "created_by",
        "status",
        "declared_size",
        "part_count",
        "created_at",
    )
    list_filter = ("status", "content_type")
    search_fields = ("original_filename", "object_key", "created_by__username")
    readonly_fields = (
        "id",
        "multipart_upload_id",
        "object_key",
        "created_at",
        "updated_at",
        "completed_at",
        "aborted_at",
    )
