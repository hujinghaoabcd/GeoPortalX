<script setup lang="ts">
import type { Map } from 'maplibre-gl';
import { onBeforeUnmount, onMounted, ref } from 'vue';

import { fetchHealth, fetchVectorLayerSource } from './api/client';
import { createMap } from './map/createMap';
import {
  addVectorLayerPreview,
  removeVectorLayerPreview,
  type PreviewHandles,
} from './map/vectorPreview';

const mapContainer = ref<HTMLElement | null>(null);
const backendStatus = ref('checking');
const previewStatus = ref('idle');
const previewLayerId = new URLSearchParams(window.location.search).get('vectorLayer');
let map: Map | undefined;
let previewHandles: PreviewHandles | undefined;

onMounted(async () => {
  if (mapContainer.value) {
    map = createMap(mapContainer.value);
    map.once('load', async () => {
      if (!map || !previewLayerId) {
        return;
      }
      previewStatus.value = 'loading';
      try {
        const descriptor = await fetchVectorLayerSource(previewLayerId);
        previewHandles = addVectorLayerPreview(map, descriptor);
        previewStatus.value = 'loaded';
      } catch {
        previewStatus.value = 'unavailable';
      }
    });
  }

  try {
    const health = await fetchHealth();
    backendStatus.value = health.status;
  } catch {
    backendStatus.value = 'unavailable';
  }
});

onBeforeUnmount(() => {
  if (map && previewHandles) {
    removeVectorLayerPreview(map, previewHandles);
  }
  map?.remove();
});
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div>
        <strong>GeoPortalX</strong>
        <span>Geospatial portal and web mapping platform</span>
      </div>
      <div class="status-group">
        <div
          v-if="previewLayerId"
          class="status"
          :data-status="previewStatus"
        >
          Vector preview: {{ previewStatus }}
        </div>
        <div class="status" :data-status="backendStatus">API: {{ backendStatus }}</div>
      </div>
    </header>
    <section ref="mapContainer" class="map" aria-label="GeoPortalX map" />
  </main>
</template>
