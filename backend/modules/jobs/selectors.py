from uuid import UUID

from django.db.models import QuerySet

from modules.accounts.models import User

from .models import Job


def jobs_for_user(user: User) -> QuerySet[Job]:
    queryset = Job.objects.select_related("created_by", "resource")
    return queryset if user.is_superuser else queryset.filter(created_by=user)


def job_for_user(user: User, job_id: UUID) -> Job | None:
    return jobs_for_user(user).filter(pk=job_id).first()
