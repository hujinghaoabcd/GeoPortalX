import type { LayerSpecification, Map } from 'maplibre-gl';

import type {
  MapLibreLayerTemplate,
  VectorLayerSourceResponse,
  VectorStyleResponse,
} from '../api/client';

export type PreviewHandles = {
  sourceId: string;
  layerIds: string[];
};

export function addVectorLayerPreview(
  map: Map,
  descriptor: VectorLayerSourceResponse,
  style: VectorStyleResponse,
): PreviewHandles {
  const compactId = descriptor.layer_id.replaceAll('-', '');
  const sourceId = `gx-vector-${compactId}`;
  const layerIds = style.maplibre_layers.map(
    (_, index) => `${sourceId}-style-${style.style.revision}-${index}`,
  );

  removeVectorLayerPreview(map, {
    sourceId,
    layerIds: findPreviewLayerIds(map, sourceId),
  });
  map.addSource(sourceId, descriptor.source);

  style.maplibre_layers.forEach((template, index) => {
    map.addLayer(buildLayerSpecification(
      template,
      layerIds[index],
      sourceId,
      descriptor.source_layer,
    ));
  });

  map.fitBounds(
    [
      [descriptor.bounds[0], descriptor.bounds[1]],
      [descriptor.bounds[2], descriptor.bounds[3]],
    ],
    {
      padding: 64,
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

function buildLayerSpecification(
  template: MapLibreLayerTemplate,
  layerId: string,
  sourceId: string,
  sourceLayer: string,
): LayerSpecification {
  return {
    id: layerId,
    type: template.type,
    source: sourceId,
    'source-layer': sourceLayer,
    paint: template.paint,
  } as LayerSpecification;
}

function findPreviewLayerIds(map: Map, sourceId: string): string[] {
  return (map.getStyle().layers ?? [])
    .filter((layer) => layer.id.startsWith(`${sourceId}-style-`))
    .map((layer) => layer.id);
}
