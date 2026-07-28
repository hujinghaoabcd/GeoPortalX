from collections.abc import Callable
from typing import Any

JobHandler = Callable[[Any, dict[str, Any]], dict[str, Any] | None]

_JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(job_type: str):
    """Register one executable handler for a stable job type identifier."""

    def decorator(handler: JobHandler) -> JobHandler:
        if not job_type or len(job_type) > 100:
            raise ValueError("Job type must be between 1 and 100 characters")
        if job_type in _JOB_HANDLERS:
            raise ValueError(f"Job handler already registered: {job_type}")
        _JOB_HANDLERS[job_type] = handler
        return handler

    return decorator


def get_job_handler(job_type: str) -> JobHandler:
    try:
        return _JOB_HANDLERS[job_type]
    except KeyError as exc:
        raise ValueError(f"Unknown job type: {job_type}") from exc


def is_registered_job_type(job_type: str) -> bool:
    return job_type in _JOB_HANDLERS


def registered_job_types() -> tuple[str, ...]:
    return tuple(sorted(_JOB_HANDLERS))
