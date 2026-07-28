import { Map, NavigationControl } from 'maplibre-gl';

export function createMap(container: HTMLElement): Map {
  const map = new Map({
    container,
    style: 'https://demotiles.maplibre.org/style.json',
    center: [118.78, 32.04],
    zoom: 8,
  });

  map.addControl(new NavigationControl(), 'top-right');
  return map;
}
