# GeoPortalX Roadmap

## Milestone M0 — Repository foundation

Status: in progress

- [x] Create clean `GeoPortalX` repository
- [x] Fix MapLibre GL JS as the only 2D renderer
- [x] Document modular-monolith architecture
- [x] Document technology stack and project structure
- [x] Document staged development plan
- [ ] Create backend and frontend applications
- [ ] Create Docker Compose development stack
- [ ] Add CI and code-quality configuration
- [ ] Add initial handoff and architecture decision records

## Milestone M1 — Platform core

- [ ] Custom UUID user model
- [ ] Organizations, memberships, groups and roles
- [ ] Unified Resource model
- [ ] Central permission engine
- [ ] Persistent Job ledger
- [ ] Celery routing and progress reporting
- [ ] MinIO/S3 storage abstraction
- [ ] Audit log foundation

## Milestone M2 — Vector publishing

- [ ] Upload sessions
- [ ] Shapefile/GeoPackage/GeoJSON inspection
- [ ] PostGIS import pipeline
- [ ] Spatial indexes and statistics
- [ ] Martin MVT publishing
- [ ] MapLibre vector rendering
- [ ] Feature identify and download
- [ ] Default vector styling

## Milestone M3 — Raster publishing

- [ ] GeoTIFF inspection
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
