# GeoPortalX Vector Export and Signed Delivery Handoff — 2026-07-28

## Branch and pull request

- Base commit on `main`: `1b249d730236a443e0f2a39c5442a189e5e8cfbd`.
- Development branch: `agent/vector-export-delivery`.
- Pull request: `#7 Add asynchronous vector exports and signed delivery`.
- The pull request remains Draft and must not be merged until all final CI checks pass and the user explicitly requests the merge.

## Purpose of this phase

This phase adds durable, asynchronous vector downloads without sending large result files through Django. A request becomes a permanent `VectorExport`, a Celery worker generates the file from the canonical PostGIS table, the result is published to S3/MinIO, and Django issues a short-lived signed GET URL only after rechecking current download permission.

```text
VectorLayer
  -> VectorExport record
  -> permanent Job ledger
  -> vector Celery queue
  -> isolated temporary workspace
  -> ogr2ogr GeoJSON / CSV / GeoPackage
  -> SHA-256 and output-size checks
  -> UUID-derived S3 object
  -> current DOWNLOAD permission check
  -> short-lived signed GET
```

## Fixed architecture decisions

1. Export generation is asynchronous and runs only in the worker process.
2. Django API workers do not stream or buffer generated export files.
3. `Resource` remains the permission authority. Public `VIEW` visibility does not imply `DOWNLOAD` permission.
4. Every signed download request re-evaluates current `DOWNLOAD` permission; a permission revoked after export creation prevents new signatures.
5. Export records are visible only to their creator, except for superusers.
6. User-controlled titles, slugs and file names never become object-store paths or database identifiers.
7. The canonical source is the registered PostGIS table. Source upload files are not re-read for export.
8. Exports currently require canonical EPSG:4326 vector layers.
9. Export objects have finite retention and are removed by an explicit cleanup command while audit metadata remains.

## Persistent model

The `vector_exports.VectorExport` model records:

- export UUID;
- source `VectorLayer`;
- creator;
- permanent `Job`;
- format and lifecycle state;
- selected attribute fields;
- optional WGS84 bbox;
- bucket, object key, version ID and ETag;
- SHA-256 checksum;
- content type, file name and byte size;
- failure code and message;
- started, completed and expiry timestamps.

Supported formats:

```text
GEOJSON
CSV
GEOPACKAGE
```

Lifecycle:

```text
PENDING -> RUNNING -> READY -> EXPIRED
                    -> FAILED
                    -> CANCELLED
```

The service also synchronizes `PENDING` and `RUNNING` records with the permanent Job ledger so broker failures, queued cancellation and worker failures cannot leave stale export states indefinitely.

## Export creation and permission rules

`create_vector_export` performs all checks before dispatch:

- requester is authenticated;
- layer is visible through the central resource policy;
- requester currently has `DOWNLOAD` or an implying action;
- layer is `READY` and stored as EPSG:4326;
- format is supported;
- requested fields are an exact whitelist from persisted `field_schema`;
- bbox is finite, ordered and inside WGS84 longitude/latitude limits;
- active per-user export count is below the configured limit.

The created Job uses:

```text
job_type=vector-export
queue=vector
max_retries=0
```

Automatic retries are intentionally disabled because the worker may have completed an expensive database export or object upload before a transport-level failure. A future idempotency/reconciliation phase may safely add retries.

## File generation

`modules.vector_exports.exporter` invokes `ogr2ogr` without a shell. PostgreSQL credentials are provided through libpq environment variables rather than command-line connection strings.

For GeoJSON and GeoPackage, the SQL result contains the native geometry column. For CSV, the SQL explicitly generates:

```sql
ST_AsText(geom) AS "WKT"
```

This avoids GDAL-version-dependent geometry column naming and provides a stable CSV contract.

Optional bbox filtering:

- GeoJSON and GeoPackage use `ogr2ogr -spat`;
- CSV embeds a validated `ST_MakeEnvelope` plus `&&` and `ST_Intersects` in controlled SQL because the CSV result deliberately has no native geometry field.

All results are ordered by the internal `gx_fid` for deterministic output.

The subprocess:

- runs in an isolated temporary directory;
- checks cooperative cancellation every 0.5 seconds;
- continuously drains stdout/stderr through repeated `communicate(timeout=...)` calls;
- enforces a hard export timeout;
- terminates and then kills the child if cancellation or timeout occurs;
- verifies the expected output file exists and is non-empty;
- enforces the maximum result byte size.

## Object publication and signed delivery

`modules.object_storage.publication.publish_file`:

1. calculates SHA-256 locally;
2. uploads with the canonical boto3 client;
3. stores the checksum in S3 object metadata;
4. runs `HEAD` after upload;
5. verifies the remote byte size;
6. returns bucket, key, size, ETag, version ID, content type and checksum.

Object keys are generated only from UUIDs:

```text
exports/{creator_uuid}/{export_uuid}/{export_uuid}.{extension}
```

The human-readable file name is used only in `Content-Disposition` and is sanitized for the ASCII fallback. UTF-8 names are carried through `filename*`.

`GET /download` returns a temporary signed S3 GET URL. Its validity is the smaller of:

- `S3_PRESIGNED_URL_EXPIRY`;
- the remaining export retention period.

## API

```text
POST /api/v1/vector-exports/
GET  /api/v1/vector-exports/
GET  /api/v1/vector-exports/{export_id}
POST /api/v1/vector-exports/{export_id}/cancel
GET  /api/v1/vector-exports/{export_id}/download
```

Create payload:

```json
{
  "layer_id": "VectorLayer UUID",
  "export_format": "GEOJSON",
  "fields": ["name", "speed"],
  "bbox": [118.0, 31.0, 119.0, 33.0]
}
```

The API returns export metadata and Job ID, not result bytes. Clients should poll the export or Job endpoint until `READY`, then request a signed download.

## Configuration

```text
VECTOR_EXPORT_TIMEOUT=3600
VECTOR_EXPORT_MAX_BYTES=5368709120
VECTOR_EXPORT_RETENTION_SECONDS=604800
VECTOR_EXPORT_MAX_ACTIVE_PER_USER=5
VECTOR_EXPORT_MAX_FIELDS=200
```

Existing `S3_PRESIGNED_URL_EXPIRY` controls maximum individual download-signature duration.

## Expiry and cleanup

Run:

```bash
python manage.py purge_expired_vector_exports --limit 500
```

The command:

- selects expired or retention-ended ready exports;
- deletes the S3 object;
- marks the record `EXPIRED`;
- clears live object addressing fields;
- retains format, size, checksum, timestamps and other audit metadata;
- reports deletion failures without falsely clearing object metadata.

This command should later be scheduled through Celery Beat or an operations scheduler.

## Validation

The implemented code path has passed:

- migration drift check;
- Ruff and Python compilation;
- API creation, ownership, current permission and cancellation tests;
- public-view-without-download denial;
- revoked download permission blocking a new signature;
- real PostGIS source table export through system `ogr2ogr`;
- real GeoJSON content validation;
- real CSV field and explicit `WKT` validation;
- real GeoPackage SQLite feature-table validation;
- optional bbox returning only intersecting features;
- real MinIO generated-file upload;
- remote HEAD size validation;
- real pre-signed GET download and `Content-Disposition` validation;
- existing Martin, dataset-inspection and frontend regressions.

The final CI status must be copied into the PR body after the documentation commit completes.

## Known limitations

- Export results are single files; Shapefile ZIP is not currently offered as an output format.
- Exports are limited to one `VectorLayer`; multi-layer dataset packages are not implemented.
- Current bbox exports use WGS84 and canonical EPSG:4326 storage only.
- Attribute filtering supports field selection but not arbitrary expressions or sorting.
- The API currently uses polling; WebSocket/SSE progress delivery is not implemented.
- Purge is a management command and is not yet automatically scheduled.
- If a worker disappears after object upload but before database finalization, an orphan export object can remain. Reconciliation is a later operations task.
- The current Job policy disables automatic retry for export tasks.

## Next tasks

1. Add persistent default vector styles, legends and field-driven classification.
2. Add MapLibre popup/identify integration and a full dataset preview panel.
3. Add DatasetVersion replacement, activation and rollback.
4. Add orphan export/staging-table reconciliation and scheduled cleanup.
5. Implement GeoTIFF-to-COG conversion and protected TiTiler publication.
6. Build the frontend dataset catalog, uploader progress and Job Center.
