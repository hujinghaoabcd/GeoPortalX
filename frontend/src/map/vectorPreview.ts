import type { LayerSpecification, Map } from 'maplibre-gl';

import type { VectorLayerSourceResponse } from '../api/client';

export type PreviewHandles = {
  sourceId: string;
  layerIds: string[];
};

export function addVectorLayerPreview(
  map: Map,
  descriptor: VectorLayerSourceResponse,
): PreviewHandles {
  const compactId = descriptor.layer_id.replaceAll('-', '');
  const sourceId = `gx-vector-${compactId}`;
  const layerIds = [`${sourceId}-primary`, `${sourceId}-outline`];

  removeVectorLayerPreview(map, { sourceId, layerIds });
  map.addSource(sourceId, descriptor.source);

  const geometryType = descriptor.geometry_type.toUpperCase();
  const primary = buildPrimaryLayer(
    sourceId,
    layerIds[0],
    descriptor.source_layer,
    geometryType,
  );
  map.addLayer(primary);

  if (geometryType.includes('POLYGON')) {
    map.addLayer({
      id: layerIds[1],
      type: 'line',
      source: sourceId,
      'source-layer': descriptor.source_layer,
      paint: {
        'line-color': '#183153',
        'line-width': 1.25,
      },
    });
  } else {
    layerIds.pop();
  }

  map.fitBounds(
    [
      [descriptor.bounds[0], descriptor.bounds[1]],
      [descriptor.bounds[2], descriptor.bounds[3]],
    ],
    {
      padding: 48,
      maxZoom: 13,
      duration: 700,
    },
  );
  return { sourceId, layerIds };
}

export function removeVectorLayerPreview(map: Map, handles: PreviewHandles): void {
  for (const layerId of handles.layerIds) {
    if (map.getLayer(layerId)) {
      map.removeLayer(layerId);
    }
  }
  if (map.getSource(handles.sourceId)) {
    map.removeSource(handles.sourceId);
  }
}

function buildPrimaryLayer(
  sourceId: string,
  layerId: string,
  sourceLayer: string,
  geometryType: string,
): LayerSpecification {
  if (geometryType.includes('POINT')) {
    return {
      id: layerId,
      type: 'circle',
      source: sourceId,
      'source-layer': sourceLayer,
      paint: {
        'circle-radius': 5,
        'circle-color': '#1261a0',
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1,
      },
    };
  }
  if (geometryType.includes('POLYGON')) {
    return {
      id: layerId,
      type: 'fill',
      source: sourceId,
      'source-layer': sourceLayer,
      paint: {
        'fill-color': '#3f8fc5',
        'fill-opacity': 0.42,
      },
    };
  }
  return {
    id: layerId,
    type: 'line',
    source: sourceId,
    'source-layer': sourceLayer,
    paint: {
      'line-color': '#1261a0',
      'line-width': 2.5,
    },
  };
}
