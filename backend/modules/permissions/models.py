import uuid

from django.conf import settings
from django.db import models


class PermissionAction(models.TextChoices):
    VIEW = "VIEW", "View"
    DOWNLOAD = "DOWNLOAD", "Download"
    EDIT = "EDIT", "Edit"
    PUBLISH = "PUBLISH", "Publish"
    MANAGE = "MANAGE", "Manage"
    OWNER = "OWNER", "Owner"


class PermissionSubjectType(models.TextChoices):
    USER = "USER", "User"
    GROUP = "GROUP", "Organization group"
    ORGANIZATION = "ORGANIZATION", "Organization"
    AUTHENTICATED = "AUTHENTICATED", "Authenticated users"
    ANONYMOUS = "ANONYMOUS", "Anonymous users"
    SHARE_LINK = "SHARE_LINK", "Share link"


class ResourcePermission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(
        "resources.Resource",
        on_delete=models.CASCADE,
        related_name="permission_grants",
    )
    subject_type = models.CharField(
        max_length=24,
        choices=PermissionSubjectType.choices,
        db_index=True,
    )
    subject_id = models.UUIDField(null=True, blank=True, db_index=True)
    action = models.CharField(
        max_length=16,
        choices=PermissionAction.choices,
        db_index=True,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resource_permissions_granted",
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("resource", "subject_type", "subject_id", "action"),
                name="resource_permission_unique",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        subject_type__in=(
                            PermissionSubjectType.AUTHENTICATED,
                            PermissionSubjectType.ANONYMOUS,
                        ),
                        subject_id__isnull=True,
                    )
                    | models.Q(
                        subject_type__in=(
                            PermissionSubjectType.USER,
                            PermissionSubjectType.GROUP,
                            PermissionSubjectType.ORGANIZATION,
                            PermissionSubjectType.SHARE_LINK,
                        ),
                        subject_id__isnull=False,
                    )
                ),
                name="resource_permission_subject_shape",
            ),
        ]
        indexes = [
            models.Index(
                fields=("resource", "subject_type", "subject_id"),
                name="resource_permission_subject_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource_id}:{self.subject_type}:{self.action}"
