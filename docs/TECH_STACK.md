# GeoPortalX Technology Stack

## Version policy

- Prefer stable releases with active security support.
- Prefer long-term-support releases for the core web framework.
- Pin major and minor versions in project manifests and exact transitive versions in lock files.
- Upgrade patch releases through automated dependency pull requests after CI passes.
- Major upgrades require an architecture decision record and migration test.

## Frontend

| Concern | Technology | Decision |
|---|---|---|
| Framework | Vue 3 | Composition API and TypeScript |
| Build | Vite | ESM-first frontend build |
| State | Pinia | Domain-focused stores |
| Routing | Vue Router | Route-level code splitting |
| 2D map | MapLibre GL JS 6 | Only supported 2D map renderer |
| Styling | MapLibre Style Specification | Embedded in GeoPortalX Map Document |
| Charts | Apache ECharts | Dashboard and map-linked charts |
| HTTP | Fetch wrapper | Typed API client generated from OpenAPI where practical |
| Validation | Zod | Runtime validation for documents and API payloads |
| Testing | Vitest + Playwright | Unit, component and end-to-end testing |

MapLibre GL JS 6 is ESM-only and requires WebGL2. GeoPortalX targets modern browsers accordingly.

## Backend

| Concern | Technology | Decision |
|---|---|---|
| Runtime | Python 3.13 | Standard project runtime |
| Framework | Django 5.2 LTS | Stable LTS business platform |
| API | Django Ninja | Typed REST and OpenAPI |
| Database | PostgreSQL | Business and metadata data |
| Spatial database | PostGIS | Canonical editable vector storage |
| PostgreSQL driver | psycopg 3 | Pool-capable modern driver |
| Tasks | Celery 5.6 | Distributed background execution |
| Broker | Redis | Message transport and short cache |
| Object storage | MinIO/S3 | Original files, COG, previews and exports |
| Geospatial I/O | GDAL, Rasterio, Pyogrio | Import, conversion and inspection |
| Geometry | Shapely, GeoPandas | Processing layer |
| Metadata protocol | pycsw | Embedded CSW compatibility |
| Authentication | Django auth + OIDC adapter | Local identity first, federation optional |
| Testing | pytest, pytest-django | Backend test suite |
| Formatting/lint | Ruff | Formatting and linting |
| Type checking | mypy | Service-layer type checks |

## Map publishing

| Data | Canonical storage | Publishing service | Browser consumer |
|---|---|---|---|
| Vector | PostGIS | Martin | MapLibre MVT source |
| Raster | COG on MinIO/S3 | TiTiler | MapLibre raster source |
| Small temporary vector | GeoJSON | Django/static object | MapLibre GeoJSON source |
| Archived tiles | PMTiles/MBTiles | Martin or signed object access | MapLibre source adapter |

## Standards

### Required

- OGC API - Records
- OGC API - Features
- OGC API - Tiles or compatible TileJSON interfaces
- CSW 2.0.2
- STAC catalog/item interoperability
- ISO 19115/19139 export
- Dublin Core and DCAT export

### Optional compatibility profile

- WMS
- WMTS
- WFS
- WFS-T
- WCS
- SLD

The optional profile may use GeoServer, but GeoServer does not own platform identity, permissions, resources or metadata.

## Infrastructure

| Concern | Technology |
|---|---|
| Local orchestration | Docker Compose |
| Gateway | Caddy initially; Nginx supported |
| CI | GitHub Actions |
| Package management | uv for Python, pnpm for frontend |
| Containers | Multi-stage OCI images |
| Observability | Structured JSON logs; Prometheus profile later |
| Secrets | Environment files locally; secret manager in production |

## Explicit exclusions

- No OMap dependency.
- No OpenLayers dependency.
- No mandatory GeoServer.
- No mandatory Elasticsearch/OpenSearch in the first release.
- No large binary payloads in Redis or PostgreSQL JSON fields.
- No microservice decomposition before operational evidence supports it.
