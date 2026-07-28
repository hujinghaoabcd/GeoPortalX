# GeoPortalX Dataset Registration and PostGIS Import Handoff — 2026-07-28

## Branch and pull request

- Base commit: `0d9466333368841a0d292f1cbc58982b988875e0` on `main`.
- Development branch: `agent/dataset-registration-import`.
- Pull request: `#5 Register datasets and import vectors into PostGIS`.
- The pull request remains Draft and is not merged.

## Purpose of this phase

The upload and inspection phases produced permanent UploadSession and Job records, but the
platform still needed a durable domain object representing a usable geospatial dataset. This
phase adds that layer and completes the first vector path from an inspected upload into a
validated PostGIS table.

The implemented flow is:

```text
Resource
  -> completed UploadSession
  -> successful inspection Job
  -> Dataset
  -> immutable DatasetVersion
  -> VectorLayer or RasterDataset registration
  -> vector-import Job when applicable
  -> staging PostGIS table
  -> validation/index/statistics
  -> canonical PostGIS table
```

## Fixed architecture decisions

1. `Resource` remains the single catalog, ownership, visibility and permission object.
2. `Dataset` stores domain lifecycle state and has exactly one Resource.
3. `DatasetVersion` is an immutable record of one source upload and one inspection result.
4. `VectorLayer` is the independently publishable unit used by Martin and later feature APIs.
5. `RasterDataset` stores source metadata now; COG publication is a later phase.
6. Source upload objects remain in S3/MinIO and are not duplicated during registration.
7. User-controlled dataset and layer names are display metadata only. PostgreSQL identifiers
   are generated from UUIDs.
8. Vector data never enters a canonical table until staging validation succeeds.
9. PostgreSQL remains the source of truth for dataset state; Celery transports execution only.

## Persistent models

Module: `backend/modules/datasets/`

### Dataset

- UUID primary key.
- One-to-one link to Resource.
- Kind: `VECTOR` or `RASTER`.
- Status: `REGISTERED`, `IMPORTING`, `READY`, `FAILED`, `ARCHIVED`.
- Current DatasetVersion.
- Permanent failure code and message.

### DatasetVersion

- UUID primary key.
- Monotonic version number within one Dataset.
- One-to-one source UploadSession.
- One-to-one inspection Job.
- Source format and SHA-256.
- Complete inspection result JSON.
- Created-by user and timestamps.
- Status and permanent failure information.

The one-to-one source upload constraint makes registration idempotent and prevents the same
uploaded object from silently becoming multiple dataset versions.

### VectorDataset and VectorLayer

VectorDataset stores registered/imported layer counts. Each VectorLayer stores:

- source layer name, driver, CRS, bounds and field schema;
- geometry type and geometry column;
- imported SRID and feature count;
- canonical PostgreSQL schema and table;
- WGS84 extent;
- lifecycle and failure information.

### RasterDataset

RasterDataset persists:

- dimensions and band count;
- driver, CRS and EPSG;
- bounds and affine transform;
- band metadata;
- image-structure tags;
- COG readiness assessment;
- future published bucket and key fields.

Raster registration becomes `READY` after successful inspection because source persistence is
already complete. COG conversion and TiTiler publication remain separate jobs.

## Dataset registration service

Entry point:

```python
register_dataset_from_inspection(...)
```

Validation includes:

- actor owns the inspection Job or is a superuser;
- Job status is `SUCCEEDED`;
- Job type is `vector-inspect` or `raster-inspect`;
- result payload contains an upload and inspection document;
- UploadSession exists and is `COMPLETED`;
- upload owner matches inspection-job owner;
- Job Resource and UploadSession Resource match;
- existing Resource type matches the inspected dataset kind;
- actor has `EDIT` permission on an existing Resource;
- selected vector layers exist, are unique and are spatial.

Registration is transactional. Validation failure leaves no partial Resource, Dataset or
DatasetVersion rows.

## Dataset API

```text
GET  /api/v1/datasets/
POST /api/v1/datasets/register
GET  /api/v1/datasets/{dataset_id}
```

Registration accepts:

- inspection Job ID;
- title, slug and description;
- visibility and optional organization;
- optional selected vector layer names;
- whether vector import should start immediately.

List and detail queries reuse the central Resource permission queryset. A user cannot discover a
Dataset whose Resource is not accessible.

## Vector import Job

Stable Job type: `vector-import`.

Queue: `vector`.

Input payload:

```json
{
  "dataset_version_id": "UUID"
}
```

The Worker revalidates the DatasetVersion, Resource association, creator permission and upload
ownership. Database credentials, source paths and table names are never accepted from the client.

## PostGIS import flow

For every selected VectorLayer:

```text
worker temporary source
  -> ogr2ogr
  -> geoportalx_staging.stg_<layer_uuid>
  -> geometry_columns validation
  -> feature count
  -> information_schema field discovery
  -> WGS84 extent calculation
  -> GiST geometry index
  -> ANALYZE
  -> ALTER TABLE SET SCHEMA geoportalx_data
  -> RENAME TO v_<layer_uuid>
```

Known source CRS values are transformed to EPSG:4326 during import. The source is passed to
`ogr2ogr` as a process argument. PostgreSQL credentials are supplied through libpq environment
variables derived from Django's database configuration.

The command is executed without a shell and with a configurable timeout.

## Identifier safety

Canonical identifiers use only generated values:

```text
geoportalx_staging.stg_<32 hex characters>
geoportalx_data.v_<32 hex characters>
```

Schema names are administrator settings and must pass a strict lowercase PostgreSQL identifier
regular expression. User titles, slugs, filenames and source layer names are never interpolated
into SQL identifiers.

All SQL identifiers are validated and quoted before use.

## State transitions

Successful vector path:

```text
Dataset REGISTERED -> IMPORTING -> READY
DatasetVersion REGISTERED -> IMPORTING -> READY
VectorLayer REGISTERED -> IMPORTING -> READY
Resource DRAFT -> PROCESSING -> READY
```

Failure path:

```text
Dataset -> FAILED
DatasetVersion -> FAILED
VectorLayer -> FAILED
Resource -> FAILED
```

Failure cleanup drops staging and already promoted canonical tables for all layers in the version.
The error class and message are permanently recorded.

Cooperative cancellation removes generated tables and returns the registration to:

```text
Dataset REGISTERED
DatasetVersion REGISTERED
VectorLayer REGISTERED
Resource DRAFT
```

## Settings

```text
DATASET_DB_SCHEMA=geoportalx_data
DATASET_STAGING_SCHEMA=geoportalx_staging
OGR2OGR_EXECUTABLE=ogr2ogr
VECTOR_IMPORT_TIMEOUT=3600
```

The backend/worker image already contains system GDAL and `ogr2ogr`.

## Validation completed

The current branch passes all five CI jobs:

- `backend-quality`: frozen dependencies, Ruff and Python compilation;
- `backend-test`: migration drift, full PostGIS pytest suite and real `ogr2ogr` import;
- `storage-integration`: live MinIO multipart roundtrip;
- `dataset-inspection`: real Pyogrio GeoJSON and Rasterio GeoTIFF inspection;
- `frontend`: frozen pnpm install, TypeScript and Vite production build.

The real vector import test verifies that a GeoJSON LineString becomes a canonical PostGIS layer
with:

- `geom` geometry column;
- SRID 4326;
- one imported feature;
- discovered attribute fields;
- persisted schema/table metadata;
- READY Dataset, DatasetVersion, VectorLayer and Resource states;
- a non-null Resource spatial extent.

## Known limitations

- Only the initial DatasetVersion registration path is exposed; replacement versions are not yet
  implemented.
- A user-facing CRS override is not available for sources without an embedded CRS.
- Nonspatial layers are inspected but are not registered as VectorLayers in this phase.
- Geometry validity, duplicate detection and per-field descriptive statistics are not yet stored.
- Martin source descriptors and MVT access endpoints are not yet exposed.
- Dataset table reconciliation after an external database administrator changes tables is not yet
  implemented.
- Raster registration does not yet create a COG or publish through TiTiler.
- Frontend dataset registration and import-progress screens are not yet implemented.

## Next tasks

1. Add geometry-quality and field-statistics jobs for imported VectorLayers.
2. Expose permission-aware Martin source/tile descriptors without bypassing Django authorization.
3. Add vector preview, identify and download endpoints.
4. Implement DatasetVersion replacement and rollback semantics.
5. Add orphan staging-table and stale import reconciliation commands.
6. Implement GeoTIFF-to-COG conversion, object publication and TiTiler integration.
7. Surface registration, inspection and import progress in the frontend Job Center.
