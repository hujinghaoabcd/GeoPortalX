from dataclasses import dataclass
from uuid import UUID

from .services import ensure_job_not_cancelled, report_job_progress


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    """Worker-facing helpers backed by the permanent PostgreSQL job ledger."""

    job_id: UUID

    def report_progress(self, progress: int, message: str = "") -> None:
        report_job_progress(
            job_id=self.job_id,
            progress=progress,
            message=message,
        )

    def ensure_not_cancelled(self) -> None:
        ensure_job_not_cancelled(job_id=self.job_id)
