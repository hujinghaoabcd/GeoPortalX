# GeoPortalX Job Execution Lifecycle Handoff — 2026-07-28

## Scope completed

This stage connects Celery execution to the permanent PostgreSQL `Job` ledger. Redis and
Celery transport work messages, but they are not treated as the source of truth for job status,
progress, errors, retries, cancellation or results.

## State machine

```text
PENDING -> QUEUED -> RUNNING -> SUCCEEDED
                       |  |
                       |  +-> FAILED
                       |  +-> CANCELLED
                       +-> RETRYING -> RUNNING
                                      |  |  |
                                      |  |  +-> CANCELLED
                                      |  +----> FAILED
                                      +-------> RETRYING
```

Terminal states are `SUCCEEDED`, `FAILED` and `CANCELLED`. Terminal jobs cannot transition to
another state.

## Permanent Job fields

The Job model now stores:

- Celery task ID and selected queue
- current status and priority
- numeric progress and human-readable progress message
- input parameters and structured result payload
- output resource references
- error code and error message
- retry count and maximum retries
- cancellation request time
- worker heartbeat time
- start and finish timestamps
- creating user and optional related Resource

## Dispatch order

1. The application validates the registered `job_type`, queue, priority and retry limit.
2. The Job row is created in `PENDING` state inside a database transaction.
3. `transaction.on_commit()` registers dispatch after the transaction succeeds.
4. Dispatch generates the Celery task ID and persists `QUEUED` before sending to Redis.
5. If broker delivery fails, the Job is moved from `QUEUED` to `FAILED` with
   `JOB_DISPATCH_FAILED`.

Persisting `QUEUED` before broker delivery avoids a fast Worker starting before the permanent
ledger knows the task ID and queue state.

## Job handler registry

Business tasks do not create separate Celery entrypoints. They register stable handlers through:

```python
@register_job_handler("vector-import")
def import_vector(context, parameters):
    context.report_progress(20, "Inspecting source data")
    context.ensure_not_cancelled()
    ...
    return {"dataset_id": "..."}
```

The single Celery entrypoint is `modules.jobs.tasks.execute_job`. The first built-in handler is
`health-probe`, used to validate API, broker and Worker integration.

Future handlers should use stable, documented job type identifiers such as:

- `vector-inspect`
- `vector-import`
- `raster-inspect`
- `raster-cog-convert`
- `thumbnail-generate`
- `catalog-index`
- `processing-execute`

## Progress and heartbeat

`JobExecutionContext.report_progress()` writes progress to PostgreSQL under a row lock. Progress
must be monotonic and remain below 100 until the task reaches `SUCCEEDED`. Every progress update
also refreshes `last_heartbeat_at`.

The API and frontend must read progress from PostgreSQL, not from a Celery result backend.

## Retry semantics

Handlers raise `RetryableJobError` only for recoverable failures such as temporary object-storage,
network or external-service errors. The Worker:

1. moves `RUNNING` to `RETRYING`;
2. increments the permanent retry counter;
3. keeps the same Celery task ID through `Task.retry()`;
4. applies exponential backoff capped at 60 seconds;
5. moves the Job to `FAILED` after the configured retry limit.

Validation errors, malformed source files and deterministic processing errors must not use
`RetryableJobError`.

## Cancellation semantics

Cancellation is idempotent.

- `PENDING`, `QUEUED` and `RETRYING` jobs become `CANCELLED` immediately.
- The Celery task ID is revoked without force termination when available.
- `RUNNING` jobs retain `RUNNING` while `cancellation_requested_at` is set.
- The Worker checks cancellation before execution, during progress updates and after the handler.
- The Worker acknowledges the request by moving the Job to `CANCELLED`.

Force-killing Worker processes is intentionally not the default because GDAL/PostGIS/object-storage
operations may leave partial outputs. Handlers must clean staging outputs or remain idempotent.

## Current API

```text
GET  /api/v1/jobs/types
GET  /api/v1/jobs/
POST /api/v1/jobs/
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
```

Users can access only their own Jobs. Superusers can access all Jobs. A Job related to a Resource
can be created only when the requester can view that Resource.

## Queue policy

The standard queues are:

- `system`
- `import`
- `vector`
- `raster`
- `processing`
- `catalog`

Workers use late acknowledgement, worker-lost rejection and a prefetch multiplier of one. These
settings are appropriate for long-running geospatial processing but require idempotent handlers.

## Tests added

- validated success and failure transitions
- monotonic progress and heartbeat updates
- persistent retry counter and `RETRYING` state
- queued dispatch and Celery task ID persistence
- cooperative running-task cancellation
- cancellation before broker dispatch
- health-probe Worker execution and result persistence
- authenticated Job API creation, listing, type catalog, cancellation and user isolation

## Remaining work

1. Validate the API, Redis and Worker together through Docker Compose.
2. Add stale-heartbeat detection and operational recovery commands.
3. Add MinIO/S3 storage abstraction and bucket bootstrap.
4. Add upload sessions and register vector/raster inspection handlers.
5. Add audit events for Job creation, cancellation, retry, success and failure.
6. Add frontend Job Center views and polling or server-sent event updates.
