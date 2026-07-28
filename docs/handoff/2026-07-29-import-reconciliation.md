# GeoPortalX import reconciliation and orphan cleanup handoff

Date: 2026-07-29

Branch: `agent/import-reconciliation-cleanup`

Draft PR: #10 — Add safe vector import reconciliation and cleanup

## Goal

This phase adds an operational reconciliation layer around the existing staged vector import pipeline. The importer already uses deterministic table names:

- final table: `geoportalx_data.v_<VectorLayer UUID without hyphens>`
- staging table: `geoportalx_staging.stg_<VectorLayer UUID without hyphens>`

The new code compares permanent Django records and the Job ledger with the actual PostgreSQL schemas. It reports drift without changing anything and performs narrowly scoped cleanup only when an operator supplies explicit apply flags.

## Files

- `backend/modules/datasets/reconciliation.py`
- `backend/modules/datasets/management/commands/reconcile_vector_imports.py`
- `backend/tests/test_import_reconciliation.py`
- `docs/ROADMAP.md`

The `management` and `management/commands` package initializers were also added.

## Default behavior

The command is dry-run by default:

```bash
python manage.py reconcile_vector_imports
```

It inventories the configured data and staging schemas, correlates managed tables with `VectorLayer`, `DatasetVersion`, and `Job`, prints a human-readable report, and makes no changes.

Machine-readable output:

```bash
python manage.py reconcile_vector_imports --json
```

The JSON report contains:

- generation time and stale cutoff;
- configured data and staging schemas;
- total and managed table counts;
- protected table count;
- issue and severity counts;
- table, layer, version, and Job identifiers;
- recommended actions;
- actions actually applied.

## Explicit mutation rules

`--apply` alone is rejected. It must be combined with at least one action:

```bash
python manage.py reconcile_vector_imports \
  --apply \
  --drop-orphans
```

```bash
python manage.py reconcile_vector_imports \
  --apply \
  --fail-stale-versions
```

Both may be selected in one invocation after reviewing a dry-run report.

The default grace period is 60 minutes and can be changed with:

```bash
--stale-after-minutes 180
```

A positive value is required.

## Table safety boundary

The cleanup code recognizes only:

```text
geoportalx_data.v_[0-9a-f]{32}
geoportalx_staging.stg_[0-9a-f]{32}
```

The schema names must also pass conservative PostgreSQL identifier validation.

The command refuses to drop:

- any table in another schema;
- any table not matching the deterministic managed naming convention;
- active or historical `READY` layer tables;
- tables associated with a non-terminal import Job;
- recently created import artifacts inside the grace period;
- stale active Job artifacts, because the ledger and worker state are ambiguous.

Unmanaged tables in the two schemas are ignored and retained.

## Detected conditions

### Final and staging tables

- `ORPHAN_FINAL_TABLE`: managed final table has no `VectorLayer` row.
- `ORPHAN_STAGING_TABLE`: managed staging table has no `VectorLayer` row.
- `STALE_STAGING_TABLE`: staging table belongs to a layer but has no active Job and exceeded the grace period.
- `RECENT_STAGING_TABLE_WITHOUT_JOB`: same condition inside the grace period; report only.
- `STALE_NONREADY_FINAL_TABLE`: non-ready layer has a promoted final table, no active Job, and exceeded the grace period.
- `RECENT_NONREADY_FINAL_TABLE`: recent equivalent; report only.

### Model-to-storage drift

- `READY_LAYER_STORAGE_METADATA_MISSING`: a ready layer lacks schema, table, or tile source metadata.
- `READY_LAYER_STORAGE_NAME_MISMATCH`: ready layer metadata does not match its UUID-derived table.
- `READY_LAYER_TABLE_MISSING`: ready layer points to a physical table that does not exist.
- `NONREADY_LAYER_HAS_PUBLICATION_METADATA`: non-ready layer still carries publication metadata.

Ready-layer drift is critical and is never automatically repaired or deleted.

### Job and version drift

- `STALE_ACTIVE_IMPORT_JOB`: Job is still `PENDING`, `QUEUED`, `RUNNING`, or `RETRYING`, but its latest heartbeat or update is older than the cutoff. Report only; the operator must inspect or cancel the Job first.
- `RECENT_IMPORTING_VERSION_WITHOUT_JOB`: importing version has no active Job but is still inside the grace period.
- `STALE_IMPORTING_VERSION`: importing version has no active Job and exceeded the grace period.
- `COMPLETE_VERSION_NOT_FINALIZED`: all layer tables are ready but version pointer or status finalization did not complete. Tables are protected; rerun the import Job or activate the version after review.

## Safe table cleanup

`--apply --drop-orphans` executes only issues carrying the `DROP_ORPHAN_TABLE` recommendation.

Before every drop, the service revalidates:

- configured schema;
- managed name pattern;
- physical table existence.

PostgreSQL identifiers are quoted. The command never interpolates user-provided table names.

## Stale version failure repair

`--apply --fail-stale-versions` handles only `STALE_IMPORTING_VERSION` findings.

Inside a database transaction it:

1. locks the `DatasetVersion`;
2. rechecks that no active vector-import Job exists;
3. rechecks that the version is still `IMPORTING`;
4. locks its `VectorLayer` rows;
5. drops only their deterministic staging and final tables;
6. marks layers and version `FAILED` with `IMPORT_RECONCILIATION_STALE`;
7. clears layer publication metadata.

If the failed version is a replacement candidate and another current version is active, the Dataset and Resource remain `READY` and unchanged.

If there is no usable current version, or the stale version itself is the current pointer from older data, the pointer is cleared and the Dataset and Resource are marked failed.

## Monitoring mode

For a scheduled read-only check:

```bash
python manage.py reconcile_vector_imports \
  --json \
  --fail-on-critical
```

The command prints the report and exits nonzero when it contains critical issues. This is suitable for cron, Kubernetes CronJob, or an external monitoring wrapper.

Recommended initial operating policy:

1. run dry-run hourly or daily;
2. retain JSON output centrally;
3. alert on critical issues;
4. review stale active Jobs manually;
5. run explicit cleanup separately after review;
6. use a grace period comfortably longer than the largest expected import.

Do not schedule automatic `--fail-stale-versions` until production import durations and worker heartbeat behavior are measured.

## Tests

The PostGIS test suite creates real tables and verifies:

- dry-run detects orphan final and staging tables without deleting them;
- active and historical ready-version tables are protected;
- explicit cleanup drops managed orphan tables;
- an unmanaged table remains untouched;
- an active import staging table remains untouched;
- stale replacement failure removes only candidate tables;
- stale replacement failure preserves the current ready Dataset and Resource;
- stale active Jobs are critical report-only findings even in apply mode;
- a missing ready-layer table is critical and not mutated;
- mutating command flags are rejected without `--apply`.

CI run #137 passed backend quality, full PostGIS tests, MinIO, Martin, Pyogrio/Rasterio, TypeScript, and Vite before this documentation-only follow-up.

## Known limitations

- PostgreSQL does not expose a portable table creation timestamp, so age decisions for model-backed artifacts use layer and version timestamps. Truly orphan tables require explicit operator approval regardless of age.
- The command does not inspect Celery worker control state. The permanent Job ledger is the authority; stale active ledger rows are report-only.
- `COMPLETE_VERSION_NOT_FINALIZED` is not automatically activated because activation changes publication state and requires operator intent.
- Reports are emitted to stdout or JSON but are not yet stored in a dedicated audit model.
- No automatic scheduler is installed by this phase.

## Next phase

The next major roadmap item is raster publication:

1. create immutable raster versions;
2. convert GeoTIFF sources to Cloud Optimized GeoTIFF;
3. build overviews and statistics;
4. publish COG objects to MinIO/S3;
5. integrate an internal TiTiler service behind GeoPortalX permission checks;
6. add MapLibre raster preview and point query.
