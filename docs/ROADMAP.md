# GeoPortalX Roadmap

## Milestone M0 — Repository foundation

Status: implementation ready for review

- [x] Create clean `GeoPortalX` repository
- [x] Fix MapLibre GL JS as the only 2D renderer
- [x] Document modular-monolith architecture
- [x] Document technology stack and project structure
- [x] Document staged development plan
- [x] Create backend and frontend applications
- [x] Create Docker Compose development stack
- [x] Add CI and code-quality configuration
- [x] Add initial handoff and architecture decision records
- [x] Validate dependency installation, migrations, tests and frontend production build in CI
- [x] Generate and review initial migrations
- [x] Commit reproducible backend and frontend dependency lockfiles
- [ ] Run the full Docker Compose stack and service-to-service integration tests

## Milestone M1 — Platform core

- [x] Custom UUID user model and initial migration
- [x] Organizations, memberships, groups and roles
- [x] Unified Resource model and initial migration
- [x] Central resource permission model and evaluation service
- [x] Persistent Job ledger and initial migration
- [x] Validated, row-locked Job lifecycle transitions
- [x] Celery dispatch, progress, retry and cooperative cancellation integration
- [x] Permission-filtered Resource queryset and service layer
- [x] Authenticated organization and resource APIs
- [x] Authenticated Job list, create, detail and cancel APIs
- [x] MinIO/S3 storage abstraction and bucket bootstrap
- [x] Persistent direct-upload sessions and multipart API
- [ ] Audit log foundation

## Milestone M2 — Vector publishing

- [x] Upload sessions
- [x] Shapefile/GeoPackage/GeoJSON inspection
- [x] Persistent Dataset, DatasetVersion and VectorLayer registration
- [x] Staged PostGIS import pipeline
- [x] GiST spatial indexes and PostgreSQL statistics
- [x] Bounded geometry-quality and field-statistics profiles
- [x] Permission-aware Martin TileJSON and MVT proxy
- [x] Internal-only Martin deployment and deterministic source IDs
- [x] Minimal MapLibre vector preview rendering
- [x] Permission-aware feature detail, keyset pagination, bbox query and identify
- [x] Asynchronous GeoJSON/CSV/GeoPackage export and signed delivery
- [ ] Dataset version replacement and rollback
- [ ] Import reconciliation and orphan-table cleanup
- [ ] Full dataset browser and preview controls
- [ ] Editable default vector styling

## Milestone M3 — Raster publishing

- [x] GeoTIFF inspection
- [ ] COG conversion
- [ ] Overviews and statistics
- [ ] MinIO/S3 publication
- [ ] TiTiler integration
- [ ] MapLibre raster rendering
- [ ] Raster point query and render settings

## Milestone M4 — Map Studio

- [ ] GeoPortalX Map Document schema
- [ ] MapLibre source/layer lifecycle
- [ ] Layer tree and grouping
- [ ] Style editor
- [ ] Labels, legends, filters and popups
- [ ] Drawing, measure and identify
- [ ] Save, version, publish and share
- [ ] Public map viewer

## Milestone M5 — Catalog and standards

- [ ] ResourceMetadata
- [ ] Catalog search and facets
- [ ] OGC API - Records
- [ ] CSW 2.0.2 through pycsw
- [ ] OGC API - Features
- [ ] STAC representation
- [ ] ISO, Dublin Core and DCAT exports
- [ ] Metadata harvesters

## Milestone M6 — Editing and processing

- [ ] Feature editing
- [ ] ChangeSet and rollback
- [ ] Processing registry
- [ ] ProcessRun and lineage
- [ ] Core spatial tools
- [ ] pyKDEX plugin
- [ ] pyGWRx plugin

## Milestone M7 — Applications

- [ ] Dashboards
- [ ] Stories
- [ ] Application templates
- [ ] Printing and export
- [ ] Optional Cesium scenes

## Milestone M8 — Compatibility and operations

- [ ] Optional GeoServer profile
- [ ] WMS/WFS/WCS/WMTS/SLD adapter
- [ ] OIDC and enterprise identity
- [ ] Quotas and governance
- [ ] Backup and restore
- [ ] Monitoring and performance benchmarks
- [ ] Scale-out deployment guidance

## Release targets

- `0.1.0`: platform foundation and authenticated resource catalog
- `0.2.0`: vector upload, publishing and MapLibre viewing
- `0.3.0`: raster COG publishing and viewing
- `0.4.0`: Map Studio
- `0.5.0`: metadata standards and harvesters
- `0.6.0`: editing and processing plugins
- `0.7.0`: dashboards, stories and applications
- `1.0.0`: documented, tested and operationally supported full platform baseline
