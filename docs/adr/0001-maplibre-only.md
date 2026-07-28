# ADR 0001: Use MapLibre GL JS as the only 2D map engine

- Status: Accepted
- Date: 2026-07-28

## Context

GeoPortalX needs a modern browser renderer for vector tiles, raster tiles, GeoJSON, style expressions, terrain and interactive map authoring. Reusing OMap or supporting both OpenLayers and MapLibre would create duplicated layer, interaction, style and lifecycle abstractions.

## Decision

GeoPortalX uses MapLibre GL JS as its only 2D map renderer.

The frontend map module is internal to GeoPortalX and does not attempt to reproduce the complete MapLibre API. It adds only platform-specific document, resource, permission and authoring behavior.

## Consequences

- MapLibre Style JSON is the embedded map styling representation.
- Vector publication prioritizes MVT.
- Raster publication prioritizes web tile endpoints from TiTiler.
- Modern browsers with WebGL2 are required for MapLibre GL JS 6.
- OMap and OpenLayers are not dependencies.
- Optional Cesium integration remains separate for 3D scenes.
