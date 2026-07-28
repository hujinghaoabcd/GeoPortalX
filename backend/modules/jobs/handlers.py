from typing import Any

from .context import JobExecutionContext
from .registry import register_job_handler


@register_job_handler("health-probe")
def run_health_probe(
    context: JobExecutionContext,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Small built-in job used to verify broker and worker integration."""

    context.report_progress(25, "Worker accepted the job")
    context.ensure_not_cancelled()
    context.report_progress(75, "Health probe completed")
    return {
        "status": "ok",
        "echo": parameters,
    }
