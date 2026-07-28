# GeoPortalX Architecture

## 1. Product scope

GeoPortalX is a geospatial data portal and web mapping platform. It aims to provide the functional categories commonly expected from GeoNode and ArcGIS-style portals while reducing deployment and synchronization complexity.

The platform covers:

- users, organizations, groups and roles;
- resource catalog and metadata discovery;
- vector, raster and tabular data management;
- online map authoring and publishing;
- external geospatial service registration;
- dashboards, stories, applications and optional 3D scenes;
- asynchronous imports, exports and spatial processing;
- permissions, audit, versioning and data lineage;
- modern and legacy geospatial interoperability protocols.

## 2. Architectural style

GeoPortalX starts as a **modular monolith**.

- One Django deployment owns core business rules.
- Domain modules communicate through Python service interfaces, not internal HTTP calls.
- Performance-sensitive tile delivery is delegated to Martin and TiTiler.
- Celery workers execute long-running and compute-heavy operations.
- Optional compatibility services are isolated behind adapters.

Microservices are not introduced until independent scaling or operational isolation is justified by measurements.

## 3. System context

```text
Browser / API client / Desktop GIS
                |
          Gateway / Proxy
                |
     +----------+-----------+
     |          |           |
  Frontend   Django API   OGC endpoints
     |          |           |
     |       Permission gateway
     |          |
     +----------+-------------------------------+
                |               |               |
             Martin          TiTiler          pycsw
                |               |               |
             PostGIS        COG on S3      ResourceMetadata
                |
       PostgreSQL business data

Django -> Redis broker -> Celery workers
Workers -> PostgreSQL / PostGIS / MinIO-S3
```

## 4. Required services

### Core deployment

- `gateway`: Caddy or Nginx reverse proxy;
- `frontend`: Vue 3 single-page application;
- `backend`: Django ASGI application;
- `worker`: Celery workers;
- `scheduler`: Celery Beat;
- `postgis`: PostgreSQL with PostGIS;
- `redis`: Celery message broker and short-lived cache;
- `minio`: S3-compatible object storage;
- `martin`: vector tile service for PostGIS MVT;
- `titiler`: dynamic COG raster tile service.

### Optional deployment profiles

- `geoserver`: legacy WMS, WFS, WCS, WMTS and SLD compatibility;
- `search`: OpenSearch/Elasticsearch when PostgreSQL full-text and facets are insufficient;
- `keycloak`: enterprise identity federation when required;
- `cesium`: optional 3D scene module;
- monitoring stack: Prometheus, Grafana and centralized logs.

Optional services must never become the source of truth for resources, users, permissions or job state.

## 5. Core domain model

### 5.1 Resource

Every managed and publishable object is represented by one `Resource` record.

Resource types include:

- `VECTOR_DATASET`
- `RASTER_DATASET`
- `TABLE_DATASET`
- `MAP`
- `SCENE`
- `DASHBOARD`
- `STORY`
- `DOCUMENT`
- `SERVICE`
- `APPLICATION`
- `ANALYSIS_RESULT`

Shared concerns are implemented once around `Resource`:

- ownership and organization;
- lifecycle and visibility;
- permission grants;
- metadata and search indexing;
- favorites, comments and sharing;
- thumbnails and distributions;
- versions, relations and lineage;
- audit events.

### 5.2 Dataset specializations

`VectorDataset` stores the database schema, table, geometry column, geometry type, SRID, feature count, fields, spatial extent and tile-source configuration.

`RasterDataset` stores source and COG object URIs, CRS, bounds, dimensions, resolution, bands, NoData, statistics and default rendering configuration.

`TableDataset` stores relational table information and field metadata without requiring geometry.

### 5.3 Map document

A map is stored as a versioned `GeoPortalX Map Document` containing:

- camera state;
- an embedded MapLibre Style document;
- layer groups and tree state;
- popup and identify configuration;
- legends and classifications;
- filters and temporal configuration;
- widgets and interactions;
- print and export settings.

The embedded MapLibre style remains standards-compatible, while platform-specific configuration lives outside the style object.

## 6. Identity, organizations and permissions

GeoPortalX uses a custom UUID user model from the first migration.

Core identity objects:

- `User`
- `Organization`
- `OrganizationMember`
- `Group`
- `Role`
- `ApiKey`
- `Quota`

Resource permissions:

- `VIEW`
- `DOWNLOAD`
- `EDIT`
- `PUBLISH`
- `MANAGE`
- `OWNER`

Permission subjects:

- user;
- group;
- organization;
- all authenticated users;
- anonymous users;
- expiring share link.

Django is the only permission authority. Tile, download, feature, metadata and optional GeoServer requests are authorized through the platform gateway or signed access tokens.

## 7. Data storage rules

### Vector

- Canonical editable vector data is stored in PostGIS.
- Every geometry table receives appropriate spatial indexes.
- Large browser display is served as MVT through Martin.
- Complete feature access and editing are provided by Django REST and OGC API - Features.

### Raster

- Original uploads are retained according to storage policy.
- Publishable rasters are converted to Cloud Optimized GeoTIFF.
- COGs, thumbnails and exports are stored in MinIO/S3.
- TiTiler provides tiles, TileJSON, preview, point sampling and statistics.
- PostGIS Raster is not a default storage requirement.

### Files

Large files are never passed through Redis messages. Workers receive identifiers and retrieve objects from storage.

## 8. Asynchronous jobs

Celery and Redis are mandatory in the standard deployment.

Redis is used for message transport and short-lived coordination. PostgreSQL is the permanent job ledger.

`Job` records include:

- job and Celery task identifiers;
- job type and queue;
- actor and related resource;
- status, priority and progress;
- input parameters and output resources;
- retry count and timestamps;
- structured error code and human-readable error message.

Initial queues:

- `import`
- `vector`
- `raster`
- `processing`
- `catalog`
- `system`

## 9. Publishing pipeline

### Vector pipeline

```text
Upload -> validation -> CRS inspection -> geometry repair
-> PostGIS import -> indexes -> field statistics
-> default style -> thumbnail -> Resource publication
-> Martin MVT -> MapLibre GL JS
```

### Raster pipeline

```text
Upload -> GDAL inspection -> CRS/band/NoData detection
-> overviews -> COG conversion -> MinIO/S3
-> statistics -> default rendering -> thumbnail
-> TiTiler -> MapLibre GL JS
```

## 10. Catalog and metadata

`ResourceMetadata` is the single authoritative metadata model.

It contains identification, abstract, keywords, themes, spatial and temporal extents, CRS, lineage, licence, constraints, contacts, distributions, relations and extensible fields.

Protocol adapters transform this model into:

- platform REST/OpenAPI;
- OGC API - Records;
- CSW 2.0.2 through embedded pycsw;
- STAC;
- ISO 19115/19139;
- Dublin Core;
- DCAT.

Metadata is not duplicated into separate protocol-specific business tables.

## 11. External services

`ExternalService` represents WMS, WMTS, WFS, OGC API services, ArcGIS REST services, STAC catalogs, XYZ, TileJSON, PMTiles and remote COG sources.

The platform stores endpoint, type, authentication, capabilities snapshot, health status, last check time and adapter configuration.

## 12. Editing, versions and lineage

Vector edits pass through Django services and support optimistic locking, review and rollback.

`ChangeSet` stores before/after attributes and geometry for each operation.

Processing provenance is represented through:

- `ResourceVersion`
- `ResourceRelation`
- `ProcessRun`
- input resources, parameters, tool version and output resources.

Every processing output is registered as a new Resource or Resource version.

## 13. Processing plugin boundary

Processing tools implement a stable contract:

```python
class ProcessingTool:
    id: str
    input_schema: dict
    output_schema: dict

    def validate(self, inputs): ...
    def execute(self, context, inputs): ...
    def register_outputs(self, result): ...
```

pyKDEX, pyGWRx and future GeoAI packages integrate through this boundary and are not hard-coded into core modules.

## 14. API boundaries

Business APIs use `/api/v1/`.

Standards endpoints use:

- `/ogc/records/`
- `/ogc/features/`
- `/ogc/tiles/`
- `/stac/`
- `/csw/`

All externally visible APIs publish OpenAPI descriptions where the protocol permits.

## 15. Non-negotiable decisions

1. MapLibre GL JS is the only 2D rendering engine.
2. GeoPortalX does not depend on OMap or OpenLayers.
3. Django is the authority for users, resources and permissions.
4. PostgreSQL stores permanent job state; Redis does not.
5. PostGIS is canonical for editable vector data.
6. COG on object storage is canonical for published raster data.
7. GeoServer remains optional and compatibility-focused.
8. Protocol adapters never become parallel sources of truth.
