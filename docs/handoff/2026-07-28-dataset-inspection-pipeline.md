# GeoPortalX Dataset Inspection Pipeline Handoff — 2026-07-28

## Branch and scope

- Base: `main` at `03d64d47388f9cc7d8a997e7cebbc758294b6bc0`.
- Development branch: `agent/dataset-inspection-pipeline`.
- Scope: materialize completed upload objects inside workers and inspect supported vector and raster formats before publication.

## Fixed design decisions

- Django remains the control plane. Geographic file parsing executes only in Celery workers.
- Celery messages carry only `upload_id`; credentials, object bytes and local paths are never serialized into the broker.
- Every handler revalidates upload ownership, completion state and optional Resource consistency.
- An uploaded object is streamed into an isolated temporary directory, never into a shared user-controlled path.
- SHA-256 is calculated during materialization, avoiding a second full pass over the source object.
- Inspection reads metadata only. It does not load complete vector features or raster arrays into memory.
- Inspection results are stored in the permanent PostgreSQL `Job.result_payload`.
- Inspection does not yet create a Dataset or publish data. Import and publication are separate later stages.

## Worker materialization

`modules.object_storage.services.download_object`:

- downloads through S3 `GetObject`;
- supports a recorded bucket and object version ID;
- streams in bounded chunks;
- writes a random `.part` file;
- calculates SHA-256 while streaming;
- flushes and `fsync`s before publication;
- verifies the downloaded byte count against `ContentLength`;
- uses `os.replace` for atomic local publication;
- closes the S3 response body and removes partial files on failure.

`modules.dataset_inspection.materialization.materialize_completed_upload`:

- requires `UploadStatus.COMPLETED`;
- creates one `TemporaryDirectory` per execution;
- uses only a conservative source extension in the local filename;
- verifies the stored size against `actual_size` or `declared_size`;
- verifies the declared SHA-256 when supplied;
- deletes the entire workspace when the context exits.

## Job input and authorization

Supported job types:

```text
vector-inspect -> vector queue
raster-inspect -> raster queue
```

Input:

```json
{
  "upload_id": "<UploadSession UUID>"
}
```

Worker checks:

1. `upload_id` is a valid UUID.
2. The UploadSession exists.
3. The job creator owns the upload, unless the creator is a superuser.
4. The upload is `COMPLETED`.
5. If the Job has a Resource, it matches the UploadSession Resource.

The generic Job API therefore cannot be used to inspect another user's object by guessing an UploadSession UUID.

## Upload inspection API

```text
POST /api/v1/uploads/{upload_id}/inspect
```

The endpoint:

- uses the existing owner-scoped UploadSession selector;
- requires a completed upload;
- selects the job type from the conservative filename extension;
- uses the `vector` or `raster` queue;
- passes only the UploadSession UUID;
- returns HTTP 202 with the created Job summary.

Supported dispatch mapping:

| Upload extension | Job type | Queue |
|---|---|---|
| `.geojson`, `.json`, `.gpkg`, `.zip` | `vector-inspect` | `vector` |
| `.tif`, `.tiff` | `raster-inspect` | `raster` |

Unsupported extensions are rejected before dispatch.

## Vector inspection

Module: `modules.dataset_inspection.vector`

Supported formats:

- GeoJSON
- GeoPackage
- Shapefile ZIP

Pyogrio is imported lazily inside the worker inspection function. The implementation uses:

- `pyogrio.list_layers` for layer discovery;
- `pyogrio.read_info` for driver, fields, geometry type, CRS and encoding;
- forced feature count and total bounds calculation;
- `/vsizip/` for a validated Shapefile archive without extracting it.

Per-layer output includes:

- name and driver;
- geometry type and geometry column name;
- FID column;
- feature count;
- CRS;
- total bounds;
- encoding;
- field names and data types.

Warnings include missing CRS, nonspatial layers and empty layers.

## Shapefile archive security

Module: `modules.dataset_inspection.archive`

Before GDAL receives a ZIP, GeoPortalX rejects:

- invalid or empty archives;
- excessive archive member counts;
- absolute paths;
- `..` traversal paths;
- Windows drive-style member names;
- encrypted members;
- symbolic links;
- excessive uncompressed byte totals;
- excessive compression ratios;
- missing `.shp` files;
- missing required `.dbf` or `.shx` sidecars.

A missing `.prj` is a warning rather than a hard failure because some valid legacy Shapefiles omit it.

The archive is not extracted to disk during inspection.

## Raster inspection

Module: `modules.dataset_inspection.raster`

Supported format:

- GeoTIFF (`.tif`, `.tiff`)

Rasterio is imported lazily. The source is opened in metadata mode; complete arrays are not read.

Output includes:

- driver;
- width, height and band count;
- CRS and EPSG where resolvable;
- bounds and affine transform;
- per-band data type, NoData, description, units, scales and offsets;
- color interpretation;
- block shapes;
- overview factors;
- stored `STATISTICS_*` metadata;
- `IMAGE_STRUCTURE` tags;
- Rasterio and GDAL versions.

A preliminary COG-readiness section reports:

- internal tiling;
- overview availability;
- compression;
- whether conversion should be scheduled.

This is not a full COG conformance validator. COG conversion and validation remain the next raster stage.

## Result payload

Successful jobs store a structure similar to:

```json
{
  "upload": {
    "id": "...",
    "original_filename": "roads.gpkg",
    "content_type": "application/geopackage+sqlite3",
    "size": 123456,
    "sha256": "...",
    "resource_id": null
  },
  "inspection": {
    "dataset_type": "vector",
    "format": "GeoPackage",
    "layer_count": 1,
    "layers": [],
    "warnings": [],
    "software": {}
  }
}
```

No object storage credentials, presigned URLs or temporary paths are returned.

## Dependency strategy

The existing `geo` extra contains future platform dependencies, including Python GDAL, GeoPandas and pycsw. The current worker image and inspection CI install the extra while explicitly skipping:

```text
gdal
geopandas
pycsw
```

This keeps Pyogrio and Rasterio available while avoiding a Python GDAL source build that may not match the system GDAL ABI. Both libraries expose their own bundled GDAL version in the inspection result for diagnostics.

Imports remain lazy, so the API process can import job registrations even in a base-dependency environment.

## Configurable safety limits

```text
DATASET_INSPECTION_MAX_ARCHIVE_MEMBERS=10000
DATASET_INSPECTION_MAX_UNCOMPRESSED_SIZE=107374182400
DATASET_INSPECTION_MAX_COMPRESSION_RATIO=100
```

Production operators should lower the uncompressed-size limit when worker ephemeral storage is smaller.

## Validation strategy

Standard backend tests cover:

- streaming object download and SHA-256 calculation;
- atomic destination publication and partial-file cleanup;
- format-to-job routing;
- safe Shapefile sidecar validation;
- archive path traversal rejection;
- UploadSession ownership and status validation;
- upload inspection API dispatch and queue selection.

The isolated `dataset-inspection` CI job installs Pyogrio and Rasterio and performs real file roundtrips:

```text
write GeoJSON -> Pyogrio list/read metadata -> verify layer metadata
write GeoTIFF -> Rasterio inspect metadata -> verify CRS/bands/bounds/COG readiness
```

The integration settings do not load Django models or PostGIS because these tests validate the file engines themselves.

## Known limitations

- GeoPackage and Shapefile real-file CI fixtures are not yet included; their structural paths are covered through Pyogrio and archive unit tests.
- Feature geometry validity is not scanned during inspection.
- Field statistics and sample values are not generated yet.
- GeoJSON `.json` classification relies on extension and then GDAL validation.
- A very large layer may require a full scan to calculate exact feature count or bounds.
- Raster statistics are reported only when already stored in metadata; pixel statistics are not calculated yet.
- COG readiness is advisory, not complete OGC COG conformance testing.
- Jobs are user-triggered through `/inspect`; upload completion does not auto-dispatch inspection.
- Inspection results are not yet copied into a permanent Dataset model.

## Next tasks

1. Add persistent `Dataset`, `VectorDataset` and `RasterDataset` models linked to Resource and UploadSession.
2. Convert a successful inspection result into a reviewed dataset registration transaction.
3. Implement vector import staging, destination schema/table naming and PostGIS import.
4. Add geometry validation/repair policy and GiST index creation.
5. Generate vector field statistics and a default MapLibre style.
6. Implement GeoTIFF-to-COG conversion, overviews and calculated band statistics.
7. Add inspection status and result views to the frontend uploader and Job Center.
8. Add expired UploadSession reconciliation and automatic inspection dispatch policy.
