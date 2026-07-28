from celery import shared_task


@shared_task(name="modules.jobs.tasks.run_health_probe")
def run_health_probe(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": "ok"}
