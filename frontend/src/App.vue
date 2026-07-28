<script setup lang="ts">
import type { Map } from 'maplibre-gl';
import { onBeforeUnmount, onMounted, ref } from 'vue';

import { fetchHealth } from './api/client';
import { createMap } from './map/createMap';

const mapContainer = ref<HTMLElement | null>(null);
const backendStatus = ref('checking');
let map: Map | undefined;

onMounted(async () => {
  if (mapContainer.value) {
    map = createMap(mapContainer.value);
  }

  try {
    const health = await fetchHealth();
    backendStatus.value = health.status;
  } catch {
    backendStatus.value = 'unavailable';
  }
});

onBeforeUnmount(() => {
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
      <div class="status" :data-status="backendStatus">API: {{ backendStatus }}</div>
    </header>
    <section ref="mapContainer" class="map" aria-label="GeoPortalX map" />
  </main>
</template>
