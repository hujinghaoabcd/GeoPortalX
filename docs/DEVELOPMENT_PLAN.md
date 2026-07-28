# GeoPortalX Development Plan

## Delivery method

Development proceeds through vertical slices. Each slice must include domain model, service logic, API, frontend behavior, permissions, tests, documentation and deployment impact where applicable.

A feature is not complete when only a database table or UI mock exists.

## Phase 0: Foundation

Goals:

- establish repository standards and architecture decisions;
- create Django and Vue applications;
- create Docker Compose development environment;
- add PostGIS, Redis, MinIO and gateway;
- configure Celery workers and scheduler;
- add health checks, structured logging and CI;
- implement custom UUID user before the first migration;
- create the initial `Resource` and `Job` models.

Exit criteria:

- one command starts the development stack;
- frontend can call backend health API;
- backend, worker, database, Redis and object storage report healthy;
- migrations and automated tests run in CI;
- no OMap or OpenLayers dependency exists.

## Phase 1: Identity, organizations and resources

Goals:

- local authentication and session/token APIs;
- users, organizations, memberships, groups and roles;
- unified Resource model;
- lifecycle, visibility, ownership and organization fields;
- central permission grants and permission evaluation service;
- resource list/detail/create/update APIs;
- initial catalog page.

Exit criteria:

- private, organization and public resources behave correctly;
- permission tests cover anonymous, member, editor and owner cases;
- all resource types use the same authorization path.

## Phase 2: Vector data vertical slice

Goals:

- multipart upload sessions and object storage staging;
- Shapefile, GeoPackage and GeoJSON inspection;
- Celery vector import pipeline;
- PostGIS table creation and spatial indexes;
- field, geometry, CRS, extent and feature statistics;
- Martin source configuration;
- MVT display in MapLibre;
- feature list, identify and download;
- default point, line and polygon styles.

Exit criteria:

- a user can upload a vector file, observe progress and view the published layer;
- failed imports produce structured errors and preserve diagnostic information;
- unauthorized users cannot access tiles or features.

## Phase 3: Raster data vertical slice

Goals:

- GeoTIFF inspection;
- COG conversion and overview generation;
- MinIO/S3 publication;
- raster statistics and default render definitions;
- TiTiler integration;
- MapLibre raster display, opacity and band/render controls;
- point sampling and preview.

Exit criteria:

- a user can upload GeoTIFF, observe conversion and view the result;
- COG and original-file retention policies are explicit;
- access is permission controlled.

## Phase 4: Map Studio

Goals:

- GeoPortalX Map Document schema and validation;
- resource browser and layer addition;
- layer tree, ordering, groups and visibility;
- point, line, fill, symbol and raster styling;
- labels, filters, legends and popups;
- identify, select, measure and drawing tools;
- save, autosave, version, publish, clone and share;
- map thumbnails and embeddable viewer.

Exit criteria:

- maps round-trip without losing configuration;
- MapLibre Style JSON remains valid;
- published versions are immutable until explicitly replaced.

## Phase 5: Metadata and catalog interoperability

Goals:

- ResourceMetadata model and editor;
- full-text, spatial, temporal and faceted search;
- contacts, distributions, constraints, lineage and relations;
- OGC API - Records;
- embedded pycsw with permission-filtered CSW queries;
- STAC and ISO/DCAT exports;
- metadata harvesters for CSW, STAC, OGC Records and ArcGIS REST.

Exit criteria:

- one metadata record produces all supported representations;
- protocol results never leak unauthorized resources;
- harvested records preserve source identity and synchronization state.

## Phase 6: Editing, versions and processing

Goals:

- feature create/update/delete APIs;
- optimistic locking and ChangeSet history;
- review and rollback workflow;
- processing tool registry;
- generic processing UI and job execution;
- ProcessRun and lineage registration;
- initial geoprocessing tools;
- plugin integration boundary for pyKDEX and pyGWRx.

Exit criteria:

- processing outputs are registered resources;
- provenance identifies input versions, parameters and tool version;
- edits are auditable and reversible.

## Phase 7: Applications and visualization

Goals:

- dashboards and linked charts;
- story maps;
- reusable application templates;
- public sharing and embedding;
- print/export workflows;
- optional Cesium scene module.

## Phase 8: Compatibility and enterprise profile

Goals:

- optional GeoServer adapter and deployment profile;
- WMS/WFS/WCS/WMTS/SLD compatibility;
- OIDC/SAML enterprise identity integration;
- quotas and organizational governance;
- advanced audit, backup and restore;
- monitoring, scaling and performance benchmarks;
- optional OpenSearch catalog backend.

## Engineering rules

1. Every write workflow is implemented in a service function with an explicit transaction boundary.
2. API handlers do not contain geospatial processing logic.
3. Celery tasks accept identifiers, not large payloads.
4. Long-running work always creates a persistent Job record.
5. Every resource-affecting endpoint calls the central permission engine.
6. Public protocol endpoints use the same permission-filtered selectors as REST APIs.
7. Database migrations are reviewed as production code.
8. New protocol adapters map to the internal model; they do not create a second business model.
9. Tests accompany each domain service and permission rule.
10. Every major task updates `docs/handoff/`.
