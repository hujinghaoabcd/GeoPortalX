from uuid import UUID

from django.db.models import QuerySet

from modules.accounts.models import User

from .models import UploadSession


def uploads_visible_to(user: User) -> QuerySet[UploadSession]:
    queryset = UploadSession.objects.select_related("resource", "created_by")
    if user.is_superuser:
        return queryset
    return queryset.filter(created_by=user)


def upload_visible_to(user: User, upload_id: UUID) -> UploadSession | None:
    return uploads_visible_to(user).filter(pk=upload_id).first()
