import { Map, NavigationControl, ResourceType } from 'maplibre-gl';

import { isGeoPortalApiUrl } from '../api/client';

export function createMap(container: HTMLElement): Map {
  const map = new Map({
    container,
    style: 'https://demotiles.maplibre.org/style.json',
    center: [118.78, 32.04],
    zoom: 8,
    transformRequest: (url, resourceType) => ({
      url,
      credentials:
        isGeoPortalApiUrl(url) && resourceType === ResourceType.Tile
          ? 'include'
          : 'same-origin',
    }),
  });

  map.addControl(new NavigationControl(), 'top-right');
  return map;
}
