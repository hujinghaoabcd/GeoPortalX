# GeoPortalX Map Document foundation handoff

Date: 2026-07-29

Repository: `hujinghaoabcd/GeoPortalX`

Branch: `agent/map-document-foundation`

Draft PR: #12 — Add persistent versioned Map Document foundation

## Purpose

This development unit starts Milestone M4 — Map Studio by introducing the persistent backend contract that future MapLibre editing, saving, versioning, sharing and public viewing will use.

The unit deliberately stops before the full visual editor. Its responsibility is to establish a stable, permission-safe and versioned Map Document foundation so later frontend work does not persist ad hoc MapLibre state or bypass the existing Resource and Dataset lifecycle systems.

## Recovered authoritative project state

The correct repository is `hujinghaoabcd/GeoPortalX`.

The previously recalled version numbers, bootstrap KDE classes and PR #16 belong to another project and must not be used as GeoPortalX continuation context.

GeoPortalX development before this unit was:

- PR #1: repository and platform foundation;
- PR #2: permanent PostgreSQL Job execution lifecycle;
- PR #3: S3/MinIO multipart upload sessions;
- PR #4: vector/raster inspection;
- PR #5: Dataset registration and staged vector import;
- PR #6: protected vector publication, profiles and feature queries;
- PR #7: asynchronous vector export;
- PR #8: persistent vector styles and interactive preview;
- PR #9: Dataset version activation and rollback;
- PR #10: import reconciliation and orphan cleanup;
- PR #11: COG raster publication, protected TiTiler proxy and raster preview.

PR #11 was validated and squash-merged into `main` as:

```text
5c2da99eae850f8fb96a0dca23ffd9d45adaab0d
```

This branch was created from that exact commit.

## Architecture principles preserved

- Django remains a modular monolith.
- Every user-facing object uses the central Resource model for ownership, organization, visibility and permissions.
- Datasets retain their independent Resource boundary.
- A map never grants access to its referenced vector or raster datasets.
- PostGIS remains the authoritative database.
- MapLibre remains the only 2D browser renderer.
- Martin and TiTiler remain internal services behind permission-aware GeoPortalX APIs.
- GeoServer remains optional and is not introduced as a Map Studio dependency.
- Saved states are immutable versions rather than mutable JSON blobs.
- All mutable workflow transitions occur through transactional services, not Django admin edits.

## Data model

### MapDocument

`MapDocument` is a Resource-backed identity record.

Important fields:

- UUID primary key;
- one-to-one `resource` relation;
- nullable `current_version` pointer;
- created and updated timestamps.

The Resource has `resource_type=MAP` and continues to carry:

- owner;
- organization;
- visibility;
- lifecycle status;
- central permission grants.

### MapDocumentVersion

Each save creates a new immutable snapshot:

- monotonically increasing `version_number` scoped to the map;
- `schema_version`;
- canonical Map Document JSON;
- deterministic SHA-256 checksum;
- creator and note;
- activation/deactivation timestamps;
- activation counter.

Versions are never updated with a replacement document. Rollback changes only `MapDocument.current_version` and activation metadata.

### MapDocumentVersionActivation

Every current-version transition is recorded permanently with:

- previous version;
- target version;
- action: `INITIAL`, `SAVE`, `ROLLBACK` or `MANUAL`;
- actor;
- note;
- timestamp.

### MapLayerReference

Every Map Document version also creates normalized ordered references. These records make source validation, future catalog queries and map rendering safer than relying only on arbitrary JSON traversal.

Each reference stores:

- ordinal and stable client layer ID;
- display title;
- kind: `VECTOR` or `RASTER`;
- binding mode: `CURRENT` or `PINNED`;
- Dataset reference;
- optional pinned DatasetVersion;
- vector source layer name;
- visibility, opacity and zoom constraints;
- style, filter, popup and legend payloads.

Database constraints enforce:

- unique ordinal per Map Document version;
- unique client layer ID per Map Document version;
- opacity between 0 and 1;
- valid zoom order;
- `CURRENT` bindings without a DatasetVersion;
- `PINNED` bindings with a DatasetVersion;
- vector references with a source layer name;
- raster references without a source layer name.

## Map Document v1 schema

The first schema is defined in `backend/modules/maps/document_schema.py`.

Top-level shape:

```json
{
  "schema_version": 1,
  "view": {
    "center": [118.8, 32.0],
    "zoom": 8,
    "bearing": 0,
    "pitch": 0
  },
  "layers": [],
  "metadata": {}
}
```

The schema currently supports:

- map center, zoom, bearing and pitch;
- ordered vector and raster layers;
- current or pinned source-version binding;
- source layer name for vector data;
- visibility, opacity and zoom range;
- map-level style override;
- filter expression payload;
- popup configuration;
- legend configuration;
- bounded metadata.

Validation bounds:

- at most 200 layers;
- at most 1 MiB canonical serialized JSON;
- at most eight nested JSON levels;
- bounded object key and array counts;
- bounded strings and integers;
- no NaN or infinite floating-point values;
- valid longitude, latitude, zoom, bearing and pitch;
- unique safe client layer IDs;
- unknown schema fields are rejected.

Canonical JSON is serialized with sorted keys, compact separators and `allow_nan=False` before hashing.

## CURRENT and PINNED bindings

### CURRENT

A `CURRENT` reference follows `Dataset.current_version` when the map is read, saved or reactivated.

The normalized reference intentionally leaves `dataset_version` null. This expresses the semantic contract that the map follows the active dataset version rather than accidentally freezing the version that was active at save time.

### PINNED

A `PINNED` reference stores an explicit DatasetVersion. The service verifies that:

- the version exists;
- it belongs to the referenced Dataset;
- its status is READY;
- the associated vector layer or raster publication is READY.

Pinned versions are suitable for reproducible maps whose source content must not change when a Dataset later activates another version.

## Source validation and permission invariant

The central invariant is:

> A Map Document never grants access to a Dataset.

The service validates all referenced sources during:

- initial map creation;
- creation of every new map version;
- activation and rollback;
- full map detail disclosure.

For every reference it verifies:

1. the Dataset exists;
2. the actor currently has Resource `VIEW` permission;
3. the Dataset status is READY;
4. Dataset kind matches the map layer kind;
5. the resolved DatasetVersion is READY;
6. vector references resolve to a READY VectorLayer;
7. raster references resolve to a READY RasterPublication.

The validation is batched:

- one Dataset query;
- one Resource permission-filter query;
- one pinned DatasetVersion query;
- one vector availability query;
- one raster availability query.

This avoids per-layer permission and publication N+1 queries for maps with up to 200 layers.

### Disclosure behavior

`GET /api/v1/maps/{id}` returns the full document only when the caller can view both:

- the map Resource;
- every Dataset Resource referenced by the current Map Document version.

When any source is inaccessible or no longer renderable, the endpoint returns `404` and does not disclose:

- Dataset UUIDs;
- DatasetVersion UUIDs;
- source layer names;
- style or popup configuration;
- the full Map Document JSON.

A public map may appear in the summary list while its full document remains inaccessible until the caller also has permission for its sources. This prevents a public map from becoming an indirect private-data disclosure channel.

## Transactional write and activation flow

### Create map

1. validate and canonicalize the document;
2. batch-check all source permissions and publication states;
3. create a Resource in DRAFT state;
4. create MapDocument;
5. create version 1 and normalized references;
6. activate version 1;
7. transition Resource to READY;
8. write an `INITIAL` activation record.

All steps occur in one database transaction.

### Save new version

1. row-lock MapDocument;
2. require map Resource `EDIT` permission;
3. reject archived maps;
4. validate document and sources;
5. allocate the next version number;
6. create immutable snapshot and references;
7. optionally activate it;
8. record a `SAVE` activation.

### Activate or roll back

1. row-lock MapDocument;
2. require `EDIT` permission;
3. reject archived maps;
4. validate that the target version belongs to the map;
5. revalidate every source using current permissions and publication state;
6. deactivate the previous current version;
7. activate the target;
8. record `ROLLBACK` when moving to a lower version number, otherwise `MANUAL`.

The activation service does not downgrade a PUBLISHED Resource to READY. Only DRAFT, PROCESSING or FAILED states transition to READY.

## API

Registered under `/api/v1/maps`:

```text
GET  /api/v1/maps/
POST /api/v1/maps/
GET  /api/v1/maps/{map_document_id}
POST /api/v1/maps/{map_document_id}/versions
POST /api/v1/maps/{map_document_id}/versions/{version_id}/activate
```

### List

Returns permission-filtered map summaries without returning document source details. Version count is annotated in SQL instead of queried per map.

### Create

Creates the Resource, map, initial snapshot and activation transactionally.

### Detail

Returns current document, version history, activation history and normalized current-layer references only after source reauthorization.

### Save version

Creates a new immutable version. The request can create it without activation, although only the current version is returned as the active document.

### Activate version

Activates or rolls back to a historical version after source revalidation.

## Admin behavior

All Map Document records are registered in Django admin for diagnosis, but are service-managed and read-only:

- add is disabled;
- delete is disabled;
- every field is read-only.

This prevents administrators from bypassing schema validation, normalized references, activation history and transaction rules through direct admin form edits.

## Migration

Initial migration:

```text
backend/modules/maps/migrations/0001_initial.py
```

Dependencies:

- swappable user model;
- `resources.0001_initial`;
- `datasets.0004_raster_publication`.

The standard CI migration drift check confirms that committed models and migration state match.

## Tests

`backend/tests/test_map_documents.py` covers:

- Resource-backed map creation;
- canonical version snapshot and SHA-256;
- normalized pinned vector reference;
- activation history;
- immutable versions and rollback;
- source permission failure during creation;
- source permission recheck during activation;
- duplicate layer ID rejection;
- invalid CURRENT binding rejection;
- non-finite JSON number rejection;
- API map creation and second version save;
- private map isolation;
- public map summary visibility without private source disclosure;
- full document disclosure after an explicit source Resource grant.

## Performance decisions

- Source Resource permission is evaluated through one central permission-aware SQL query.
- Pinned versions are resolved in one query.
- Vector and raster readiness are each resolved in one query.
- Map listing version counts use SQL annotation.
- Detail prefetches versions, layer references and activation records.
- The stored JSON remains the authoritative immutable client document, while normalized references support indexed server-side validation.

## Validation

Code head before this handoff:

```text
98fb50f373ba9e33feb951f912d3f6005fa4f9b5
```

Standard CI run #167 completed successfully:

- frozen backend dependency installation;
- Ruff lint;
- Python compilation;
- frontend dependency installation;
- frontend TypeScript check;
- frontend production build;
- migration drift check;
- full PostGIS pytest suite;
- real MinIO multipart integration;
- real Martin TileJSON/MVT integration;
- real vector/raster file inspection.

Raster integration run #27 completed successfully:

- GDAL tooling;
- real GeoTIFF to COG conversion;
- MinIO publication;
- TiTiler startup;
- real info request;
- PNG tile rendering;
- point query.

This handoff document itself creates a later documentation-only PR head. The next agent must verify workflows for that final head before merging PR #12.

## Files added

```text
backend/modules/maps/__init__.py
backend/modules/maps/admin.py
backend/modules/maps/api.py
backend/modules/maps/apps.py
backend/modules/maps/document_schema.py
backend/modules/maps/migrations/__init__.py
backend/modules/maps/migrations/0001_initial.py
backend/modules/maps/models.py
backend/modules/maps/selectors.py
backend/modules/maps/services.py
backend/tests/test_map_documents.py
docs/handoff/2026-07-29-map-document-foundation.md
```

## Files updated

```text
backend/geoportalx/api.py
backend/geoportalx/settings/base.py
docs/ROADMAP.md
```

## Known scope boundaries

This unit does not yet provide:

- map-specific render/source descriptors;
- pinned vector tile or pinned raster tile proxy endpoints;
- a dataset browser;
- a layer tree;
- MapLibre source/layer lifecycle management;
- frontend map save/version controls;
- publication/share state distinct from Resource visibility;
- anonymous public map rendering;
- collaborative editing or optimistic concurrency tokens;
- map thumbnails;
- audit-log integration.

These omissions are intentional. The persistence, permission and version contracts are established first.

## Required next development unit

Recommended branch after PR #12 is merged:

```text
agent/map-source-descriptors
```

Implement permission-safe source resolution for one Map Document version.

The next unit should:

1. resolve each normalized reference to its effective DatasetVersion;
2. support both CURRENT and PINNED semantics;
3. return a map-specific vector or raster descriptor without exposing internal Martin, TiTiler or S3 locations;
4. add version-aware protected tile endpoints for pinned sources, because the existing dataset endpoints intentionally serve only `Dataset.current_version`;
5. include stable client source IDs derived from map/version/layer identity;
6. include bounds, zoom limits and geometry or raster metadata;
7. repeat both map Resource and source Resource permission checks;
8. return `404` when a map or any source is inaccessible;
9. add tests for current version changes, pinned reproducibility and mixed vector/raster maps;
10. keep all raw object keys, database tables and internal service URLs server-side.

Only after that contract is stable should the frontend multi-dataset browser and centralized MapLibre source/layer lifecycle be implemented.

## Recovery instructions

```text
Repository: hujinghaoabcd/GeoPortalX
Base main at branch creation: 5c2da99eae850f8fb96a0dca23ffd9d45adaab0d
Branch: agent/map-document-foundation
PR: #12
Validated code head: 98fb50f373ba9e33feb951f912d3f6005fa4f9b5
Code validation: standard CI #167, Raster integration #27
Roadmap: docs/ROADMAP.md
Previous handoff: docs/handoff/2026-07-29-ci-geodjango-bootstrap.md
This handoff: docs/handoff/2026-07-29-map-document-foundation.md
```

Do not resume from pyKDEX/KDE version numbers. GeoPortalX uses milestone and PR-based continuation, and the current product phase is Map Studio.
