# GeoPortalX Vector Publishing, Quality and Feature Query Handoff — 2026-07-28

## Branch and pull request

- Base commit: `627797daa0ee49f7fa0301b8fae0d2200b2baeec` on `main`.
- Development branch: `agent/vector-publishing-quality`.
- Pull request: `#6 Add protected vector publishing and quality profiles`.
- The pull request remains Draft and must not be merged until its CI checks pass and the user explicitly requests the merge.

## Purpose of this phase

The previous phase created canonical PostGIS tables and durable `VectorLayer` records. This phase turns each ready vector layer into a safe browser-facing publication and query unit while keeping Django as the only permission authority.

The implemented flow is:

```text
UploadSession
  -> DatasetVersion
  -> geoportalx_data.v_<uuid>
  -> bounded quality and field profile
  -> stable Martin source id
  -> internal Martin MVT
  -> GeoPortalX permission gateway
  -> MapLibre GL JS
  -> protected feature detail / pagination / bbox / identify
```

## Fixed architecture decisions

1. Martin is a stateless tile adapter, not a permission authority or catalog database.
2. Martin is not exposed on a host port in the standard Compose profile.
3. Only tables in `geoportalx_data` are auto-published by Martin.
4. Public and private tile and feature requests use the same GeoPortalX endpoints and central `Resource` permission policy.
5. A `VectorLayer` UUID determines the canonical PostGIS table and stable Martin source id; user titles and source layer names never become database or tile identifiers.
6. Quality profiling is bounded by configured sample and field limits. It is intended for operational diagnostics and default UI summaries, not as a substitute for a full scientific data-quality audit.
7. Interactive feature queries are currently available only for published EPSG:4326 layers.
8. Attribute pagination uses keyset pagination on the internal `gx_fid`, never deep `OFFSET` scans.
9. MapLibre GL JS remains the only two-dimensional renderer.

## VectorLayer additions

The following fields are persisted after a successful import:

- `quality_report`: geometry null, empty, invalid and valid counts; geometry-type, SRID and dimension distributions; bounded invalidity reasons;
- `field_statistics`: null, non-null and distinct counts plus type-specific summaries and bounded frequent values;
- `tile_source_id`: stable Martin source id, currently equal to the UUID-derived canonical table name;
- `min_zoom` and `max_zoom`: the published XYZ range.

Database constraints ensure that non-empty tile source IDs are unique and that `min_zoom <= max_zoom`.

## Profiling semantics and limits

The profile is calculated inside PostgreSQL before the staging table is promoted. It uses at most `VECTOR_PROFILE_SAMPLE_SIZE` rows and at most `VECTOR_PROFILE_MAX_FIELDS` attribute columns. The current sampling method is a bounded table-prefix scan without a user-controlled ordering. Therefore:

- exact counts such as the canonical `feature_count` remain full-table values;
- quality and field-profile counts may be sampled for large tables;
- every profile includes `total_feature_count`, `sample_size`, `sampled`, and `sampling_method`;
- frequent values are only calculated when sampled distinct cardinality is below the configured threshold.

Current environment settings:

```text
VECTOR_PROFILE_SAMPLE_SIZE=100000
VECTOR_PROFILE_MAX_FIELDS=50
VECTOR_PROFILE_TOP_VALUES=5
VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT=1000
VECTOR_TILE_MIN_ZOOM=0
VECTOR_TILE_MAX_ZOOM=14
```

## Protected vector publication API

The phase provides:

```text
GET /api/v1/vector-layers/{layer_id}/tilejson
GET /api/v1/vector-layers/{layer_id}/source
GET /api/v1/vector-layers/{layer_id}/quality
GET /api/v1/vector-layers/{layer_id}/tiles/{z}/{x}/{y}
```

Rules:

- only ready layers with a non-empty `tile_source_id` are addressable;
- access is resolved through the existing permission-filtered `Resource` queryset;
- inaccessible private resources return `404`, avoiding resource-existence disclosure;
- tile coordinates and zoom are checked before an upstream request;
- upstream response size is bounded;
- public resources receive short shared-cache headers;
- protected resources use `private, no-store` and vary on cookie/authorization;
- Martin discovery lag returns `503` with `Retry-After` rather than a permanent `404`.

Tile URLs include the immutable DatasetVersion UUID as a cache-busting query value.

## Protected feature query API

The phase also adds:

```text
GET /api/v1/vector-layers/{layer_id}/features
GET /api/v1/vector-layers/{layer_id}/features/{feature_id}
GET /api/v1/vector-layers/{layer_id}/identify
```

### Paginated and bbox query

`GET .../features` supports:

- `limit`: positive page size bounded by `VECTOR_FEATURE_MAX_LIMIT`;
- `cursor`: the last returned `gx_fid`; the query continues with `gx_fid > cursor`;
- `bbox=min_lon,min_lat,max_lon,max_lat`: optional WGS84 envelope filter using both the GiST-compatible bounding-box operator and exact `ST_Intersects`;
- `fields=name,type,...`: optional exact field whitelist validated against persisted `field_schema`;
- `include_geometry=false`: attribute-only pages when geometry is unnecessary.

The response is a GeoJSON `FeatureCollection` with `next_cursor`. No full-table count is executed per page; the durable `VectorLayer.feature_count` remains available separately.

### Feature detail

`GET .../features/{feature_id}` reads one internal `gx_fid`, supports the same field selection and optional geometry omission, and returns `404` when the feature is absent.

### Identify

`GET .../identify` accepts:

- `longitude` and `latitude` in WGS84;
- `tolerance_m`, bounded by `VECTOR_IDENTIFY_MAX_TOLERANCE_METERS`;
- `limit`, bounded independently by `VECTOR_IDENTIFY_MAX_LIMIT`;
- optional `fields`.

PostGIS uses geography distance for meter-based filtering and ordering. Each returned GeoJSON feature contains a foreign member `distance_m`, preserving the original attribute namespace.

### Query safety limits

```text
VECTOR_FEATURE_MAX_LIMIT=500
VECTOR_FEATURE_MAX_FIELDS=50
VECTOR_FEATURE_QUERY_TIMEOUT_MS=5000
VECTOR_FEATURE_MAX_RESPONSE_BYTES=5242880
VECTOR_IDENTIFY_MAX_LIMIT=25
VECTOR_IDENTIFY_MAX_TOLERANCE_METERS=5000
```

Safety properties:

- schema, table, geometry and field names come only from registered metadata and are quoted as database identifiers;
- user input is always passed as SQL parameters;
- each query runs inside a transaction with `SET LOCAL statement_timeout`;
- serialized JSON responses are rejected with `413` if they exceed the configured byte limit;
- invalid bbox, fields, limits, coordinates and tolerances return `400`;
- layers without safe EPSG:4326 publication metadata return `409`;
- permission checks happen before any direct PostGIS feature query.

## Martin configuration

`deploy/martin.yaml` uses an environment-expanded PostgreSQL connection, disables CORS, discovers only `geoportalx_data`, disables function publication, uses the canonical table name as the source ID, and uses `gx_fid` as the feature ID.

The standard Compose profile mounts this file and uses only `expose: 3000`; it does not publish Martin directly to the host. Django fetches MVT responses through `MARTIN_INTERNAL_URL` and returns them after permission evaluation.

Martin 1.11.0 performs environment substitution across the entire YAML text, including comments. Therefore the editor-only `# yaml-language-server: $schema=...` comment must not be restored unless the dollar sign is escaped in a Martin-compatible way.

## MapLibre preview

The minimal frontend preview is intentionally small and does not yet constitute Map Studio. It can be opened with:

```text
http://localhost:5173/?vectorLayer=<VectorLayer UUID>
```

The browser fetches the protected source descriptor, adds a MapLibre vector source, chooses a basic point/line/polygon style, fits the layer bounds, and sends credentials only for GeoPortalX API requests.

## Validation scope

The phase adds or extends tests for:

- geometry-quality and field-statistics SQL against PostGIS;
- import persistence of quality, statistics, source ID and table TileJSON comment;
- public anonymous TileJSON access;
- private-layer isolation and owner access;
- protected MVT proxy headers and coordinate validation;
- real Martin startup, table auto-discovery, catalog, TileJSON and MVT responses;
- keyset pagination beginning with a valid `gx_fid=0`;
- bbox filtering with selected fields;
- attribute-only feature detail and missing-feature handling;
- nearest-feature identify with geography distance;
- invalid field, bbox, limit and coordinate rejection;
- serialized response-size enforcement;
- frontend TypeScript and production build.

The backend static quality gate passed after the feature-query implementation. The full PostGIS test job is the authoritative validation for the new direct table queries.

## Known limitations

- The current profile is bounded and is not a random or stratified statistical sample.
- Geometry repair is not performed automatically; invalid geometries are reported.
- Martin source discovery uses a polling reload interval, so newly imported layers can briefly return retryable `503` responses.
- The tile proxy currently buffers one bounded tile response in Django; a dedicated gateway may later provide higher-throughput caching and streaming.
- Identify geography casts are appropriate for interactive bounded result sets but should be benchmarked on very large complex geometries.
- Feature queries currently require EPSG:4326 publication rather than transforming arbitrary stored CRSs per request.
- The preview has no dataset browser, legend, popup, filter or identify interface yet; only the backend identify API exists.
- Public immutable CDN caching and signed share-link tile access are not yet implemented.
- Asynchronous GeoJSON, CSV and GeoPackage export is not implemented in this phase.

## Next tasks

1. Add asynchronous GeoJSON/CSV/GeoPackage export jobs and signed result delivery.
2. Add default vector style generation, legends and field-driven classification.
3. Add DatasetVersion replacement, activation and rollback workflows.
4. Add orphan staging-table and stale-import reconciliation commands.
5. Implement GeoTIFF-to-COG conversion and protected TiTiler publication.
6. Build the frontend dataset catalog, query popup, uploader progress and Job Center.
