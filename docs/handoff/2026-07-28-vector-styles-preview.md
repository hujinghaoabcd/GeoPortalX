# GeoPortalX vector styles and interactive preview handoff

Date: 2026-07-28
Branch: `agent/vector-styles-preview`
Pull request: #8
Base: `main` at `89c44bb1f918eb3c6b0cf06a31e358305051f6bc`

## 1. Scope completed

This stage adds a persistent, permission-aware default style for every ready vector layer and upgrades the minimal MapLibre preview into an interactive dataset preview panel.

Completed capabilities:

- persistent one-to-one `VectorStyle` records;
- migration backfill for existing ready vector layers;
- automatic style creation for future ready layers;
- simple symbol rendering;
- categorical common-value classification;
- graduated equal-interval classification;
- predefined palettes;
- validated point, line and polygon symbol controls;
- server-generated MapLibre layer templates;
- server-generated legends;
- read and update style APIs;
- responsive preview panel;
- field, class-count and palette controls;
- live style refresh without recreating the map;
- PostGIS identify integration;
- safe MapLibre popup and attribute panel rendering.

## 2. Architecture decisions

### 2.1 Style records are separate domain objects

Styles are stored in the new `vector_styles` Django app instead of adding an unrestricted JSON style field to `VectorLayer`.

Relationship:

```text
VectorLayer 1 ─── 1 VectorStyle
```

The separation keeps vector storage metadata and presentation metadata independent while retaining a stable default style for each published layer.

### 2.2 Clients cannot persist arbitrary MapLibre JSON

The API accepts only bounded semantic inputs:

- style mode;
- classification field;
- supported classification method;
- class count;
- named palette;
- color, opacity and geometry-specific symbol dimensions.

The server generates MapLibre expressions and layer templates from those inputs. Stored data is never returned as an arbitrary executable MapLibre style document.

### 2.3 Classification reuses persisted profiles

The style API does not perform an unbounded database scan.

Categorical classification uses `VectorLayer.field_statistics[].top_values`.

Graduated classification uses persisted numeric `minimum` and `maximum` values.

When a selected field lacks the required bounded profile, the API returns a validation error and does not start a synchronous table scan.

### 2.4 Resource permission remains authoritative

`GET /style` uses the existing vector-layer visibility selector.

`PUT /style` additionally requires `PermissionAction.EDIT` through the central Resource permission evaluator.

Public `VIEW` access is read-only. Unauthorized update attempts return `404` to avoid leaking private resources.

## 3. Data model

New model: `modules.vector_styles.models.VectorStyle`.

Important fields:

```text
id
layer
mode
field_name
classification_method
class_count
palette
symbol
classes
fallback_symbol
revision
updated_by
created_at
updated_at
```

Supported modes:

```text
SIMPLE
CATEGORICAL
GRADUATED
```

Supported methods:

```text
UNIQUE_VALUES
EQUAL_INTERVAL
```

Database constraint:

```text
1 <= class_count <= 12
```

The public service currently restricts automatically generated classes to at most nine because each built-in palette contains nine colors.

## 4. Migration and creation behavior

Migration:

```text
backend/modules/vector_styles/migrations/0001_initial.py
```

The migration:

1. creates the `VectorStyle` table;
2. adds the class-count constraint;
3. creates a simple default style for every existing `VectorLayer` whose status is `READY`.

Future ready layers are handled by a `post_save` signal in:

```text
backend/modules/vector_styles/signals.py
```

The signal is registered by `VectorStylesConfig.ready()`.

## 5. Default symbols

### Point

```json
{
  "color": "#2563eb",
  "opacity": 0.85,
  "size": 6,
  "outline_color": "#ffffff",
  "outline_width": 1
}
```

### Polygon

```json
{
  "color": "#3b82f6",
  "opacity": 0.45,
  "outline_color": "#1d4ed8",
  "outline_width": 1
}
```

### Line

```json
{
  "color": "#2563eb",
  "opacity": 0.9,
  "width": 2.5
}
```

Fallback categories use gray by default.

## 6. Validation rules

Colors must use exact `#RRGGBB` syntax.

Bounds:

```text
opacity: 0–1
point size: 1–30
outline width: 0–8
line width: 0.5–20
class count exposed by API: 1–9
```

A style field must:

- exist in `VectorLayer.field_schema`;
- not be `gx_fid`;
- not be the geometry column;
- have a matching persisted field statistics object.

Categorical fields require non-empty `top_values`.

Graduated fields require finite numeric minimum and maximum values with `maximum > minimum`.

## 7. Built-in palettes

```text
BLUES
VIRIDIS
SPECTRAL
ORANGE
CATEGORY10
```

Each palette has nine fixed colors. Class colors are sampled evenly across the selected palette.

## 8. MapLibre template contract

The style response contains `maplibre_layers`.

Each template contains only:

```text
type
paint
```

It deliberately excludes:

```text
id
source
source-layer
arbitrary filters
arbitrary layouts
```

The frontend injects the protected source ID and Martin source-layer ID at runtime.

Categorical colors are generated with a `match` expression over the selected field converted to string.

Graduated colors are generated with a `step` expression over the selected field converted to number.

## 9. API

### Read style

```text
GET /api/v1/vector-layers/{layer_id}/style
```

Response includes:

- resource, dataset and layer identifiers;
- layer title and resource title;
- geometry type and feature count;
- normalized persistent style;
- generated legend;
- safe MapLibre templates;
- style-capable fields;
- available palettes;
- `can_edit`.

### Update style

```text
PUT /api/v1/vector-layers/{layer_id}/style
```

Example categorical request:

```json
{
  "mode": "CATEGORICAL",
  "field_name": "road_class",
  "classification_method": "UNIQUE_VALUES",
  "class_count": 5,
  "palette": "CATEGORY10",
  "symbol": {
    "color": "#2563eb",
    "opacity": 0.75
  }
}
```

Example graduated request:

```json
{
  "mode": "GRADUATED",
  "field_name": "speed",
  "classification_method": "EQUAL_INTERVAL",
  "class_count": 5,
  "palette": "VIRIDIS",
  "symbol": {
    "color": "#2563eb",
    "opacity": 0.85,
    "width": 3
  }
}
```

Every successful update increments `revision`.

## 10. Frontend preview workflow

Entry remains:

```text
http://localhost:5173/?vectorLayer=<VectorLayer UUID>
```

Load sequence:

```text
fetch protected vector source
+
fetch protected vector style
→ add MapLibre vector source
→ add generated style layers
→ fit layer bounds
→ render legend and controls
```

After a style update:

```text
PUT style
→ receive new revision and templates
→ remove only current preview layers/source
→ re-add source and new layers
```

The MapLibre base map is not recreated.

## 11. Interactive identify

Clicking the map calls:

```text
GET /api/v1/vector-layers/{layer_id}/identify
```

Current preview requests:

```text
limit=1
tolerance_m=35
```

The nearest feature is shown in:

- a MapLibre popup;
- the side-panel property table.

Popup nodes are constructed with `document.createElement()` and `textContent`. Feature values are not inserted with `innerHTML`.

## 12. Responsive UI

Desktop layout:

```text
left preview panel | map
```

Mobile layout:

```text
map
preview panel
```

The panel contains:

- resource and layer heading;
- feature count and geometry type;
- legend;
- style mode selector;
- field selector;
- color and opacity controls;
- class count and palette selectors;
- apply button when editable;
- identify result table.

## 13. Tests

New backend tests:

```text
backend/tests/test_vector_styles.py
```

Coverage includes:

- default style auto-creation for ready layers;
- simple line template and default legend;
- categorical classes from persisted common values;
- polygon fill and outline templates;
- graduated equal-interval class bounds;
- generated MapLibre `match` and `step` expressions;
- public read without edit;
- edit attempts returning `404` without permission;
- unknown classification field rejection;
- unsafe color string rejection.

CI run #127 passed all six jobs before this documentation-only commit:

- backend-quality;
- backend-test;
- storage-integration;
- dataset-inspection;
- martin-integration;
- frontend.

## 14. Files added

```text
backend/modules/vector_styles/__init__.py
backend/modules/vector_styles/apps.py
backend/modules/vector_styles/models.py
backend/modules/vector_styles/services.py
backend/modules/vector_styles/api.py
backend/modules/vector_styles/signals.py
backend/modules/vector_styles/migrations/__init__.py
backend/modules/vector_styles/migrations/0001_initial.py
backend/tests/test_vector_styles.py
```

## 15. Files modified

```text
backend/geoportalx/settings/base.py
backend/geoportalx/api.py
frontend/src/api/client.ts
frontend/src/map/vectorPreview.ts
frontend/src/App.vue
frontend/src/style.css
docs/ROADMAP.md
```

## 16. Known limitations

- categorical classes are limited by the persisted top-value profile, currently five values by default;
- graduated classification currently supports equal interval only;
- no quantile or natural-breaks classification is performed yet;
- style editing currently changes shared default layer style rather than a per-map override;
- no label rules, filters or popup configuration are persisted yet;
- the preview opens one VectorLayer supplied in the URL and is not yet a multi-dataset browser;
- the UI depends on existing session authentication and CSRF cookie behavior;
- the external demonstration base map remains the current development base style.

## 17. Recommended next stage

Proceed with DatasetVersion replacement and rollback before expanding Map Studio styling.

Recommended order:

1. create a new DatasetVersion from a completed inspection upload;
2. import new version layers to new UUID-derived PostGIS tables;
3. keep current version active while import runs;
4. activate a ready version transactionally;
5. preserve prior version tables for rollback;
6. update tile/style endpoints to resolve only active-version layers;
7. add rollback API and tests;
8. add reconciliation for abandoned staging and version tables.

After versioning is stable, continue with map-level style overrides, labels, filters and multi-layer Map Documents.
