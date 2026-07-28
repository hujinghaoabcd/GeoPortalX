import type { Map, RasterLayerSpecification, RasterSourceSpecification } from 'maplibre-gl';

import type { RasterSourceResponse } from '../api/client';

export type RasterPreviewHandles = {
  sourceId: string;
  layerId: string;
};

export function addRasterPreview(
  map: Map,
  descriptor: RasterSourceResponse,
): RasterPreviewHandles {
  const suffix = descriptor.publication_id.replaceAll('-', '');
  const sourceId = `geoportalx-raster-source-${suffix}`;
  const layerId = `geoportalx-raster-layer-${suffix}`;
  map.addSource(sourceId, descriptor.source as RasterSourceSpecification);
  const layer: RasterLayerSpecification = {
    id: layerId,
    type: 'raster',
    source: sourceId,
    paint: {
      'raster-opacity': descriptor.opacity,
      'raster-resampling': 'linear',
    },
  };
  map.addLayer(layer);
  const [west, south, east, north] = descriptor.bounds;
  map.fitBounds(
    [
      [west, south],
      [east, north],
    ],
    { padding: 48, maxZoom: descriptor.source.maxzoom },
  );
  return { sourceId, layerId };
}

export function removeRasterPreview(map: Map, handles: RasterPreviewHandles): void {
  if (map.getLayer(handles.layerId)) {
    map.removeLayer(handles.layerId);
  }
  if (map.getSource(handles.sourceId)) {
    map.removeSource(handles.sourceId);
  }
}
