from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from modules.jobs.models import Job, JobStatus
from modules.resources.models import LifecycleStatus

from .models import (
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    VectorLayer,
    VectorLayerStatus,
)

_FINAL = re.compile(r"^v_([0-9a-f]{32})$")
_STAGING = re.compile(r"^stg_([0-9a-f]{32})$")
_ACTIVE = {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING}


@dataclass(frozen=True, slots=True)
class Table:
    schema: str
    name: str
    size_bytes: int

    @property
    def key(self) -> tuple[str, str]:
        return self.schema, self.name


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: str
    summary: str
    schema: str = ""
    table: str = ""
    layer_id: UUID | None = None
    version_id: UUID | None = None
    job_id: UUID | None = None
    action: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "schema": self.schema or None,
            "table": self.table or None,
            "layer_id": str(self.layer_id) if self.layer_id else None,
            "version_id": str(self.version_id) if self.version_id else None,
            "job_id": str(self.job_id) if self.job_id else None,
            "recommended_action": self.action or None,
            "details": self.details,
        }


@dataclass(slots=True)
class Report:
    generated_at: Any
    stale_before: Any
    dataset_schema: str
    staging_schema: str
    tables_scanned: int
    managed_tables_scanned: int = 0
    protected: set[tuple[str, str]] = field(default_factory=set)
    issues: list[Issue] = field(default_factory=list)
    applied_actions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return {
            "generated_at": self.generated_at.isoformat(),
            "stale_before": self.stale_before.isoformat(),
            "dataset_schema": self.dataset_schema,
            "staging_schema": self.staging_schema,
            "tables_scanned": self.tables_scanned,
            "managed_tables_scanned": self.managed_tables_scanned,
            "protected_table_count": len(self.protected),
            "issue_count": len(self.issues),
            "severity_counts": counts,
            "issues": [issue.as_dict() for issue in self.issues],
            "applied_actions": self.applied_actions,
        }


@dataclass(frozen=True, slots=True)
class JobState:
    latest: Job | None
    active: tuple[Job, ...]
    live: tuple[Job, ...]


class ReconciliationSafetyError(RuntimeError):
    pass


def reconcile_vector_imports(
    *,
    stale_after: timedelta,
    apply: bool = False,
    drop_orphans: bool = False,
    fail_stale_versions: bool = False,
) -> Report:
    if stale_after.total_seconds() <= 0:
        raise ValueError("stale_after must be positive")
    if not apply and (drop_orphans or fail_stale_versions):
        raise ValueError("Mutating reconciliation options require apply=True")

    now = timezone.now()
    cutoff = now - stale_after
    data_schema = _schema(settings.DATASET_DB_SCHEMA)
    staging_schema = _schema(settings.DATASET_STAGING_SCHEMA)
    tables = _inventory(data_schema, staging_schema)
    table_keys = {table.key for table in tables}
    layers = list(
        VectorLayer.objects.select_related(
            "version", "version__dataset", "version__dataset__resource"
        )
    )
    layer_by_id = {layer.id: layer for layer in layers}
    version_ids = {layer.version_id for layer in layers}
    jobs = _job_states(version_ids, cutoff)
    report = Report(now, cutoff, data_schema, staging_schema, len(tables))

    finals: dict[UUID, Table] = {}
    stagings: dict[UUID, Table] = {}
    for table in tables:
        layer_id = _layer_id(table.name, final=table.schema == data_schema)
        if layer_id is None:
            continue
        report.managed_tables_scanned += 1
        (finals if table.schema == data_schema else stagings)[layer_id] = table

    _check_ready_layers(report, layers, table_keys, data_schema)
    _check_tables(report, layer_by_id, finals, stagings, jobs, cutoff)
    _check_versions(report, version_ids, jobs, table_keys, cutoff, data_schema)

    if apply and drop_orphans:
        _drop_reported_tables(report)
    if apply and fail_stale_versions:
        _fail_reported_versions(report)
    return report


def _inventory(data_schema: str, staging_schema: str) -> list[Table]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.nspname, c.relname, pg_total_relation_size(c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN (%s, %s) AND c.relkind IN ('r', 'p')
            ORDER BY n.nspname, c.relname
            """,
            [data_schema, staging_schema],
        )
        return [Table(str(s), str(t), int(size)) for s, t, size in cursor.fetchall()]


def _job_states(version_ids: set[UUID], cutoff: Any) -> dict[UUID, JobState]:
    grouped: dict[UUID, list[Job]] = {version_id: [] for version_id in version_ids}
    for job in Job.objects.filter(job_type="vector-import").order_by("-created_at"):
        try:
            version_id = UUID(str(job.input_parameters.get("dataset_version_id")))
        except (TypeError, ValueError):
            continue
        if version_id in grouped:
            grouped[version_id].append(job)
    states = {}
    for version_id, items in grouped.items():
        active = tuple(job for job in items if job.status in _ACTIVE)
        live = tuple(job for job in active if _activity(job) > cutoff)
        states[version_id] = JobState(items[0] if items else None, active, live)
    return states


def _activity(job: Job):
    return job.last_heartbeat_at or job.updated_at or job.created_at


def _check_ready_layers(
    report: Report,
    layers: list[VectorLayer],
    table_keys: set[tuple[str, str]],
    data_schema: str,
) -> None:
    for layer in layers:
        expected = f"v_{layer.id.hex}"
        if layer.status != VectorLayerStatus.READY:
            if layer.db_schema or layer.db_table or layer.tile_source_id:
                report.issues.append(
                    Issue(
                        "NONREADY_LAYER_HAS_PUBLICATION_METADATA",
                        "WARNING",
                        "Non-ready layer still has publication metadata",
                        layer.db_schema,
                        layer.db_table,
                        layer.id,
                        layer.version_id,
                    )
                )
            continue
        report.protected.add((data_schema, expected))
        if not layer.db_schema or not layer.db_table or not layer.tile_source_id:
            report.issues.append(
                Issue(
                    "READY_LAYER_STORAGE_METADATA_MISSING",
                    "CRITICAL",
                    "Ready layer is missing publication metadata",
                    layer_id=layer.id,
                    version_id=layer.version_id,
                )
            )
            continue
        if layer.db_schema != data_schema or layer.db_table != expected:
            report.issues.append(
                Issue(
                    "READY_LAYER_STORAGE_NAME_MISMATCH",
                    "CRITICAL",
                    "Ready layer does not use its deterministic table name",
                    layer.db_schema,
                    layer.db_table,
                    layer.id,
                    layer.version_id,
                    details={"expected_schema": data_schema, "expected_table": expected},
                )
            )
        if (layer.db_schema, layer.db_table) not in table_keys:
            report.issues.append(
                Issue(
                    "READY_LAYER_TABLE_MISSING",
                    "CRITICAL",
                    "Ready layer references a missing PostGIS table",
                    layer.db_schema,
                    layer.db_table,
                    layer.id,
                    layer.version_id,
                )
            )


def _check_tables(
    report: Report,
    layer_by_id: dict[UUID, VectorLayer],
    finals: dict[UUID, Table],
    stagings: dict[UUID, Table],
    jobs: dict[UUID, JobState],
    cutoff: Any,
) -> None:
    for layer_id, table in finals.items():
        layer = layer_by_id.get(layer_id)
        if layer is None:
            _orphan(report, "ORPHAN_FINAL_TABLE", table, layer_id)
        elif layer.status == VectorLayerStatus.READY:
            report.protected.add(table.key)
        else:
            _nonready_table(report, table, layer, jobs.get(layer.version_id), cutoff)

    for layer_id, table in stagings.items():
        layer = layer_by_id.get(layer_id)
        if layer is None:
            _orphan(report, "ORPHAN_STAGING_TABLE", table, layer_id)
            continue
        state = jobs.get(layer.version_id, JobState(None, (), ()))
        if state.active:
            report.protected.add(table.key)
            if not state.live:
                _stale_job(report, layer.version_id, layer.id, table, state.active[0])
        elif layer.updated_at <= cutoff:
            report.issues.append(
                Issue(
                    "STALE_STAGING_TABLE",
                    "WARNING",
                    "Staging table has no active Job after the grace period",
                    table.schema,
                    table.name,
                    layer.id,
                    layer.version_id,
                    action="DROP_ORPHAN_TABLE",
                    details={"size_bytes": table.size_bytes},
                )
            )
        else:
            report.issues.append(
                Issue(
                    "RECENT_STAGING_TABLE_WITHOUT_JOB",
                    "INFO",
                    "Recent staging table has no active Job",
                    table.schema,
                    table.name,
                    layer.id,
                    layer.version_id,
                )
            )


def _orphan(report: Report, code: str, table: Table, layer_id: UUID) -> None:
    report.issues.append(
        Issue(
            code,
            "WARNING",
            "Managed table has no VectorLayer record",
            table.schema,
            table.name,
            layer_id,
            action="DROP_ORPHAN_TABLE",
            details={"size_bytes": table.size_bytes},
        )
    )


def _nonready_table(
    report: Report,
    table: Table,
    layer: VectorLayer,
    state: JobState | None,
    cutoff: Any,
) -> None:
    state = state or JobState(None, (), ())
    if state.active:
        report.protected.add(table.key)
        return
    stale = layer.updated_at <= cutoff
    report.issues.append(
        Issue(
            "STALE_NONREADY_FINAL_TABLE" if stale else "RECENT_NONREADY_FINAL_TABLE",
            "WARNING" if stale else "INFO",
            "Non-ready layer has a final table without an active Job",
            table.schema,
            table.name,
            layer.id,
            layer.version_id,
            action="DROP_ORPHAN_TABLE" if stale else "",
            details={"size_bytes": table.size_bytes},
        )
    )


def _stale_job(
    report: Report,
    version_id: UUID,
    layer_id: UUID | None,
    table: Table | None,
    job: Job,
) -> None:
    report.issues.append(
        Issue(
            "STALE_ACTIVE_IMPORT_JOB",
            "CRITICAL",
            "Active Job ledger entry has not updated recently",
            table.schema if table else "",
            table.name if table else "",
            layer_id,
            version_id,
            job.id,
            details={"job_status": job.status, "last_activity_at": _activity(job).isoformat()},
        )
    )


def _check_versions(
    report: Report,
    version_ids: set[UUID],
    jobs: dict[UUID, JobState],
    table_keys: set[tuple[str, str]],
    cutoff: Any,
    data_schema: str,
) -> None:
    versions = DatasetVersion.objects.select_related("dataset", "dataset__resource").filter(
        pk__in=version_ids,
        status=DatasetVersionStatus.IMPORTING,
    )
    for version in versions:
        state = jobs.get(version.id, JobState(None, (), ()))
        if state.live:
            continue
        if state.active:
            _stale_job(report, version.id, None, None, state.active[0])
            continue
        layers = list(version.vector_layers.all())
        complete = bool(layers) and all(
            layer.status == VectorLayerStatus.READY
            and (data_schema, f"v_{layer.id.hex}") in table_keys
            for layer in layers
        )
        if complete:
            report.issues.append(
                Issue(
                    "COMPLETE_VERSION_NOT_FINALIZED",
                    "CRITICAL",
                    "All tables are ready but the version was not finalized",
                    version_id=version.id,
                    job_id=state.latest.id if state.latest else None,
                )
            )
            continue
        activity = max([version.created_at, *(layer.updated_at for layer in layers)])
        stale = activity <= cutoff
        report.issues.append(
            Issue(
                "STALE_IMPORTING_VERSION"
                if stale
                else "RECENT_IMPORTING_VERSION_WITHOUT_JOB",
                "CRITICAL" if stale else "WARNING",
                "Importing version has no active Job",
                version_id=version.id,
                job_id=state.latest.id if state.latest else None,
                action="MARK_STALE_VERSION_FAILED" if stale else "",
                details={"last_activity_at": activity.isoformat()},
            )
        )


def _drop_reported_tables(report: Report) -> None:
    seen = set()
    for issue in report.issues:
        key = (issue.schema, issue.table)
        if issue.action == "DROP_ORPHAN_TABLE" and key not in seen:
            seen.add(key)
            _drop(issue.schema, issue.table, report)


def _fail_reported_versions(report: Report) -> None:
    version_ids = {
        issue.version_id
        for issue in report.issues
        if issue.action == "MARK_STALE_VERSION_FAILED" and issue.version_id
    }
    for version_id in sorted(version_ids, key=str):
        _fail_version(version_id, report)


def _fail_version(version_id: UUID, report: Report) -> None:
    with transaction.atomic():
        version = (
            DatasetVersion.objects.select_for_update()
            .select_related("dataset", "dataset__resource")
            .get(pk=version_id)
        )
        active = Job.objects.filter(
            job_type="vector-import",
            status__in=_ACTIVE,
            input_parameters__dataset_version_id=str(version.id),
        ).exists()
        if active or version.status != DatasetVersionStatus.IMPORTING:
            raise ReconciliationSafetyError(f"Version {version.id} is no longer safe")
        layers = list(version.vector_layers.select_for_update())
        for layer in layers:
            _drop(_schema(settings.DATASET_STAGING_SCHEMA), f"stg_{layer.id.hex}", report)
            _drop(_schema(settings.DATASET_DB_SCHEMA), f"v_{layer.id.hex}", report)
        code = "IMPORT_RECONCILIATION_STALE"
        message = "Import had no active Job after the reconciliation grace period"
        VectorLayer.objects.filter(version=version).update(
            status=VectorLayerStatus.FAILED,
            db_schema="",
            db_table="",
            tile_source_id="",
            quality_report={},
            field_statistics=[],
            failure_code=code,
            failure_message=message,
        )
        version.status = DatasetVersionStatus.FAILED
        version.failure_code = code
        version.failure_message = message
        version.save(update_fields=("status", "failure_code", "failure_message"))
        dataset = version.dataset
        if dataset.current_version_id in {None, version.id}:
            dataset.current_version = None
            dataset.status = DatasetStatus.FAILED
            dataset.failure_code = code
            dataset.failure_message = message
            dataset.save(
                update_fields=(
                    "current_version",
                    "status",
                    "failure_code",
                    "failure_message",
                    "updated_at",
                )
            )
            dataset.resource.lifecycle_status = LifecycleStatus.FAILED
            dataset.resource.save(update_fields=("lifecycle_status", "updated_at"))
        report.applied_actions.append(
            {"action": "MARK_STALE_VERSION_FAILED", "version_id": str(version.id)}
        )


def _drop(schema: str, table: str, report: Report) -> None:
    schema = _schema(schema)
    data_schema = _schema(settings.DATASET_DB_SCHEMA)
    staging_schema = _schema(settings.DATASET_STAGING_SCHEMA)
    if schema == data_schema:
        pattern = _FINAL
    elif schema == staging_schema:
        pattern = _STAGING
    else:
        raise ReconciliationSafetyError(f"Refusing to use unmanaged schema {schema}")
    if pattern.fullmatch(table) is None:
        raise ReconciliationSafetyError(f"Refusing to drop unmanaged table {schema}.{table}")
    qualified = f"{connection.ops.quote_name(schema)}.{connection.ops.quote_name(table)}"
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"{schema}.{table}"])
        if cursor.fetchone()[0] is None:
            return
        cursor.execute(f"DROP TABLE {qualified} CASCADE")
    report.applied_actions.append({"action": "DROP_TABLE", "schema": schema, "table": table})


def _layer_id(name: str, *, final: bool) -> UUID | None:
    match = (_FINAL if final else _STAGING).fullmatch(name)
    return UUID(hex=match.group(1)) if match else None


def _schema(value: Any) -> str:
    schema = str(value or "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ReconciliationSafetyError(f"Invalid managed schema: {schema}")
    return schema
