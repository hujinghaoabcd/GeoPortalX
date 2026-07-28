# GeoPortalX raster COG and TiTiler publication handoff

Date: 2026-07-29

Branch: `agent/raster-cog-titiler`

Draft PR: #11 — Add COG raster publishing and protected TiTiler preview

## Goal

This phase turns inspected and registered GeoTIFF uploads into immutable Cloud Optimized GeoTIFF assets, publishes them to MinIO/S3, serves rendered tiles and point values through an internal TiTiler service, and exposes only permission-checked GeoPortalX endpoints to browsers.

The existing Dataset and DatasetVersion records remain the source catalog and version authority. Raster publication is a separate, retryable asynchronous lifecycle. A raster DatasetVersion can be registered and active while its publication is still pending, but raster source, tiles, rendering, and point-query endpoints remain unavailable until its `RasterPublication` reaches `READY`.

## Main files

Backend domain and worker:

- `backend/modules/datasets/models.py`
- `backend/modules/datasets/migrations/0004_raster_publication.py`
- `backend/modules/datasets/raster_services.py`
- `backend/modules/datasets/raster_handlers.py`
- `backend/modules/datasets/raster_conversion.py`
- `backend/modules/datasets/signals.py`

Permission proxy and rendering:

- `backend/modules/raster_tiles/selectors.py`
- `backend/modules/raster_tiles/rendering.py`
- `backend/modules/raster_tiles/services.py`
- `backend/modules/raster_tiles/api.py`

Frontend and deployment:

- `frontend/src/RasterApp.vue`
- `frontend/src/map/rasterPreview.ts`
- `frontend/src/api/client.ts`
- `frontend/src/main.ts`
- `deploy/compose.yaml`
- `.env.example`

Verification:

- `backend/tests/test_raster_publication.py`
- `backend/tests/test_raster_cog_conversion.py`
- `backend/tests/titiler_integration.py`
- `.github/workflows/raster.yml`

## Persistent model

### RasterPublication

One publication belongs to one DatasetVersion and records:

- RasterDataset and DatasetVersion;
- permanent Job ledger row;
- publication status;
- bucket, object key, object version, ETag, SHA-256, content type, and byte size;
- width, height, band count, CRS, EPSG, WGS84 bounds, and affine transform;
- band metadata;
- bounded statistics and histograms;
- image-structure metadata and validated COG profile;
- computed minimum and maximum zoom;
- failure code and message;
- started and completed timestamps;
- creating user.

Status lifecycle:

```text
PENDING
  ↓
PROCESSING
  ├──→ READY
  ├──→ FAILED
  └──→ CANCELLED
```

The DatasetVersion and original UploadSession are retained regardless of publication failure. A retry creates or reuses the same publication record and starts a new permanent Job when no active Job exists.

### RasterRenderSettings

One render record belongs to one RasterPublication and stores:

- `SINGLE_BAND` or `RGB` mode;
- selected bands;
- one rescale range per selected band;
- optional fixed colormap;
- nearest, bilinear, or cubic resampling;
- opacity;
- revision number and updating user.

Clients cannot store arbitrary TiTiler query strings. Every field is validated and the server generates the upstream request.

## Publication dispatch

Creating the initial RasterDataset registers a post-save callback. After the surrounding database transaction commits, the callback requests a publication for the active DatasetVersion.

The explicit API can also request or retry publication:

```text
POST /api/v1/raster-datasets/{dataset_id}/publish
```

The request service:

1. locks only the Dataset row and its non-null Resource relation;
2. reads the current version and RasterDataset separately, avoiding PostgreSQL `FOR UPDATE` outer-join restrictions;
3. requires Resource `EDIT` permission;
4. reuses a ready publication or an active publication Job;
5. otherwise queues `raster-publish` on the `raster` Celery queue.

## Worker flow

The handler accepts only:

```json
{"raster_publication_id": "UUID"}
```

It revalidates:

- publication existence;
- active DatasetVersion identity;
- Job Resource identity;
- current Resource edit permission;
- source-upload ownership.

Execution:

```text
completed UploadSession
→ isolated Worker temporary directory
→ streamed source materialization and SHA-256 validation
→ gdal_translate COG conversion
→ Rasterio COG validation
→ bounded band statistics and histogram calculation
→ deterministic MinIO/S3 publication
→ S3 HEAD size validation in publication helper
→ permanent RasterPublication metadata
→ default RasterRenderSettings
→ READY
```

Object key:

```text
rasters/{resource UUID}/{dataset-version UUID}/{publication UUID}.cog.tif
```

No filename, title, slug, or user path becomes part of the object key.

On cancellation or failure, the worker attempts to delete the deterministic output object and records permanent status and error details.

## COG conversion contract

The Worker invokes the configured `gdal_translate` executable without a shell:

```text
-of COG
COMPRESS=DEFLATE
PREDICTOR=YES
BLOCKSIZE=512
BIGTIFF=IF_SAFER
NUM_THREADS=ALL_CPUS
OVERVIEWS=IGNORE_EXISTING
```

The output is rejected when:

- no output file is created;
- the file is empty;
- the file exceeds `RASTER_COG_MAX_BYTES`;
- the dataset has no CRS;
- the output is not GeoTIFF;
- internal tiling is absent;
- image-structure metadata contradicts COG layout.

The conversion timeout is controlled by `RASTER_COG_TIMEOUT`.

## Statistics and zooms

Statistics never require a full-resolution Python array. Each band is sampled to a bounded maximum dimension controlled by `RASTER_STATISTICS_MAX_SIZE`.

Recorded values include:

- valid and total sample counts;
- valid percentage;
- minimum, maximum, mean, and standard deviation;
- 2nd and 98th percentiles;
- a 20-bin histogram.

The 2nd and 98th percentiles become the default display ranges. Three-band rasters default to RGB using the first three usable bands; other rasters default to single-band Viridis rendering.

The service transforms source bounds to EPSG:3857, estimates native pixel resolution, and derives a bounded Web Mercator zoom range between `RASTER_TILE_MIN_ZOOM` and `RASTER_TILE_MAX_ZOOM`.

## Permission-protected API

```text
POST /api/v1/raster-datasets/{dataset_id}/publish
GET  /api/v1/raster-datasets/{dataset_id}/publication
GET  /api/v1/raster-datasets/{dataset_id}/source
GET  /api/v1/raster-datasets/{dataset_id}/tilejson
GET  /api/v1/raster-datasets/{dataset_id}/rendering
PUT  /api/v1/raster-datasets/{dataset_id}/rendering
GET  /api/v1/raster-datasets/{dataset_id}/tiles/{z}/{x}/{y}.png
GET  /api/v1/raster-datasets/{dataset_id}/point
```

All public rendering endpoints resolve only a publication whose version equals `Dataset.current_version`.

The same Resource permission system used by vectors applies:

- public resources allow anonymous `VIEW`;
- private and organization resources require current permission;
- render changes require `EDIT`;
- unauthorized access returns `404`.

Tile XYZ coordinates and zooms are validated before the upstream request. TiTiler tile responses are bounded by `TITILER_MAX_TILE_BYTES`. Private responses use `private, no-store`; public responses receive a short configurable cache lifetime.

## TiTiler boundary

TiTiler is an internal Compose service and has no host port in the standard stack.

Pinned image:

```text
ghcr.io/developmentseed/titiler:2.0.2
```

The image is started explicitly with:

```text
uvicorn titiler.application.main:app --host 0.0.0.0 --port 8000 --workers 1
```

GeoPortalX constructs the internal asset URL:

```text
s3://{bucket}/{object_key}
```

The browser never receives:

- MinIO credentials;
- the raw S3 object key;
- a TiTiler URL;
- an arbitrary TiTiler query expression.

Only GeoPortalX calls `/cog/tiles/...` and `/cog/point/...` after permission and render validation.

Compose supplies TiTiler with S3 credentials and GDAL remote-range-read settings. The backend and Worker use `TITILER_INTERNAL_URL=http://titiler:8000`.

## MapLibre preview

Raster preview is isolated from the stable vector preview.

Open it with:

```text
http://localhost:5173/?rasterDataset=<Dataset UUID>
```

`frontend/src/main.ts` selects `RasterApp.vue` only when `rasterDataset` is present. Otherwise the existing vector App remains unchanged.

The raster application:

- requests the protected source and rendering descriptor;
- adds a MapLibre raster source and layer;
- fits to WGS84 publication bounds;
- supports single-band and RGB modes;
- supports band, colormap, resampling, and opacity controls;
- rebuilds only the raster source/layer after a render revision;
- queries original band values on map click;
- constructs popup content with DOM nodes and `textContent`.

## Configuration

```text
GDAL_TRANSLATE_EXECUTABLE=gdal_translate
RASTER_COG_TIMEOUT=3600
RASTER_COG_MAX_BYTES=53687091200
RASTER_STATISTICS_MAX_SIZE=1024
RASTER_TILE_MIN_ZOOM=0
RASTER_TILE_MAX_ZOOM=22
TITILER_INTERNAL_URL=http://titiler:8000
TITILER_REQUEST_TIMEOUT=20
TITILER_MAX_TILE_BYTES=20971520
RASTER_TILE_PUBLIC_CACHE_SECONDS=60
```

## Validation

The standard CI suite passed:

- frozen backend dependencies;
- Ruff and Python compilation;
- migration drift check;
- full PostGIS pytest suite;
- publication idempotency;
- active-version and visibility selectors;
- render validation and revision updates;
- protected source and rendering APIs;
- existing vector import, version, tile, style, feature, export, and reconciliation regressions;
- live MinIO regression;
- live Martin regression;
- real Pyogrio/Rasterio inspection regression;
- frontend TypeScript and Vite production build.

The dedicated raster workflow passed a real service path:

```text
create 1024 × 1024 GeoTIFF
→ GDAL COG conversion
→ verify 512 blocks and internal overviews
→ compute real statistics
→ upload COG to live MinIO
→ start TiTiler 2.0.2
→ read /cog/info from s3:// asset
→ render PNG WebMercator tile
→ query point values
```

## Known limitations

- Raster replacement-version registration and rollback remain vector-only in the current version API.
- Publication objects are immutable by convention, but no dedicated raster orphan-object reconciliation command exists yet.
- Statistics are bounded samples, not exact whole-raster distributions.
- Only fixed colormaps and single-band/RGB display are supported; hillshade, expressions, custom color maps, and multidimensional raster rendering are not exposed.
- Point query returns TiTiler's structured response without a domain-specific band-label transformation.
- No STAC Item or OGC coverage endpoint is generated yet.
- The full Compose stack has not yet been exercised in one single end-to-end CI job; services are validated through focused real-service workflows.

## Next phase

The next roadmap stage should start Map Studio rather than expanding one-dataset preview pages:

1. add persistent MapDocument and MapDocumentVersion models;
2. define a validated GeoPortalX map document schema;
3. add ordered vector/raster layer references and visibility state;
4. implement a multi-dataset browser and layer switching;
5. add MapLibre source/layer lifecycle management;
6. persist map-level style overrides, labels, filters, popup configuration, and legends;
7. add save, version, publish, share, and public viewer flows.
