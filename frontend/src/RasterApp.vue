<script setup lang="ts">
import type { Map, MapMouseEvent } from 'maplibre-gl';
import { Popup } from 'maplibre-gl';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import {
  fetchHealth,
  fetchRasterRendering,
  fetchRasterSource,
  identifyRasterDataset,
  updateRasterRendering,
  type RasterPointResponse,
  type RasterRenderingResponse,
  type RasterRenderingUpdate,
  type RasterSourceResponse,
} from './api/client';
import { createMap } from './map/createMap';
import {
  addRasterPreview,
  removeRasterPreview,
  type RasterPreviewHandles,
} from './map/rasterPreview';

const mapContainer = ref<HTMLElement | null>(null);
const backendStatus = ref('checking');
const previewStatus = ref('idle');
const previewError = ref('');
const saving = ref(false);
const descriptor = ref<RasterSourceResponse | null>(null);
const rendering = ref<RasterRenderingResponse | null>(null);
const selectedPixel = ref<RasterPointResponse | null>(null);
const datasetId = new URLSearchParams(window.location.search).get('rasterDataset');

const mode = ref<RasterRenderingUpdate['mode']>('SINGLE_BAND');
const band1 = ref(1);
const band2 = ref(2);
const band3 = ref(3);
const colormap = ref('viridis');
const resampling = ref<RasterRenderingUpdate['resampling']>('bilinear');
const opacity = ref(1);

let map: Map | undefined;
let handles: RasterPreviewHandles | undefined;
let popup: Popup | undefined;
let clickHandler: ((event: MapMouseEvent) => void) | undefined;

const selectedEntries = computed(() => Object.entries(selectedPixel.value?.result ?? {}));
const availableBands = computed(() => rendering.value?.available_bands ?? []);
const selectedBands = computed(() =>
  mode.value === 'RGB' ? [band1.value, band2.value, band3.value] : [band1.value],
);
const selectedRescale = computed(() =>
  selectedBands.value.map((band) => {
    const stats = rendering.value?.statistics.find((item) => item.band === band);
    const low = stats?.percentile_2 ?? stats?.minimum ?? 0;
    const high = stats?.percentile_98 ?? stats?.maximum ?? low + 1;
    return [low, high > low ? high : low + 1];
  }),
);

onMounted(async () => {
  try {
    backendStatus.value = (await fetchHealth()).status;
  } catch {
    backendStatus.value = 'unavailable';
  }
  if (!mapContainer.value) {
    return;
  }
  map = createMap(mapContainer.value);
  map.once('load', async () => {
    if (!datasetId || !map) {
      previewStatus.value = 'unavailable';
      previewError.value = '地址中缺少 rasterDataset 参数。';
      return;
    }
    await loadPreview();
    clickHandler = (event) => void identifyAt(event);
    map.on('click', clickHandler);
  });
});

onBeforeUnmount(() => {
  if (map && clickHandler) {
    map.off('click', clickHandler);
  }
  popup?.remove();
  if (map && handles) {
    removeRasterPreview(map, handles);
  }
  map?.remove();
});

async function loadPreview(): Promise<void> {
  if (!map || !datasetId) {
    return;
  }
  previewStatus.value = 'loading';
  previewError.value = '';
  try {
    const [source, render] = await Promise.all([
      fetchRasterSource(datasetId),
      fetchRasterRendering(datasetId),
    ]);
    descriptor.value = source;
    rendering.value = render;
    syncForm(render);
    handles = addRasterPreview(map, source);
    previewStatus.value = 'loaded';
  } catch (error) {
    previewStatus.value = 'unavailable';
    previewError.value = error instanceof Error ? error.message : '栅格预览不可用';
  }
}

async function saveRendering(): Promise<void> {
  if (!datasetId || !map || !descriptor.value || !rendering.value) {
    return;
  }
  saving.value = true;
  previewError.value = '';
  try {
    const updated = await updateRasterRendering(datasetId, {
      mode: mode.value,
      bands: selectedBands.value,
      rescale: selectedRescale.value,
      colormap_name: mode.value === 'RGB' ? null : colormap.value,
      resampling: resampling.value,
      opacity: opacity.value,
    });
    rendering.value = updated;
    syncForm(updated);
    if (handles) {
      removeRasterPreview(map, handles);
    }
    const source = await fetchRasterSource(datasetId);
    descriptor.value = source;
    handles = addRasterPreview(map, source);
  } catch (error) {
    previewError.value = error instanceof Error ? error.message : '渲染设置保存失败';
  } finally {
    saving.value = false;
  }
}

async function identifyAt(event: MapMouseEvent): Promise<void> {
  if (!datasetId || previewStatus.value !== 'loaded') {
    return;
  }
  try {
    selectedPixel.value = await identifyRasterDataset(
      datasetId,
      event.lngLat.lng,
      event.lngLat.lat,
    );
    popup?.remove();
    popup = new Popup({ closeButton: true, maxWidth: '360px' })
      .setLngLat(event.lngLat)
      .setDOMContent(buildPopup(selectedPixel.value))
      .addTo(map as Map);
  } catch {
    selectedPixel.value = null;
  }
}

function syncForm(render: RasterRenderingResponse): void {
  mode.value = render.mode;
  band1.value = render.bands[0] ?? 1;
  band2.value = render.bands[1] ?? Math.min(2, render.available_bands.length);
  band3.value = render.bands[2] ?? Math.min(3, render.available_bands.length);
  colormap.value = render.colormap_name ?? 'viridis';
  resampling.value = render.resampling;
  opacity.value = render.opacity;
}

function buildPopup(point: RasterPointResponse): HTMLElement {
  const root = document.createElement('div');
  root.className = 'feature-popup';
  const title = document.createElement('strong');
  title.textContent = rendering.value?.resource_title ?? '栅格像元';
  root.append(title);
  const list = document.createElement('dl');
  for (const [key, value] of Object.entries(point.result).slice(0, 12)) {
    const term = document.createElement('dt');
    term.textContent = key;
    const description = document.createElement('dd');
    description.textContent = formatValue(value);
    list.append(term, description);
  }
  root.append(list);
  return root;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div>
        <strong>GeoPortalX</strong>
        <span>Cloud Optimized GeoTIFF preview</span>
      </div>
      <div class="status-group">
        <div class="status" :data-status="previewStatus">Raster: {{ previewStatus }}</div>
        <div class="status" :data-status="backendStatus">API: {{ backendStatus }}</div>
      </div>
    </header>

    <section class="workspace">
      <aside class="preview-panel">
        <template v-if="rendering && descriptor">
          <div class="panel-heading">
            <p class="eyebrow">Raster preview</p>
            <h1>{{ rendering.resource_title }}</h1>
            <p>
              {{ descriptor.width.toLocaleString('zh-CN') }} ×
              {{ descriptor.height.toLocaleString('zh-CN') }} ·
              {{ descriptor.band_count }} 波段 · {{ descriptor.crs }}
            </p>
          </div>

          <section class="panel-section">
            <div class="section-heading">
              <h2>渲染</h2>
              <span v-if="!rendering.can_edit">只读</span>
            </div>
            <label>
              模式
              <select v-model="mode" :disabled="!rendering.can_edit">
                <option value="SINGLE_BAND">单波段</option>
                <option v-if="availableBands.length >= 3" value="RGB">RGB</option>
              </select>
            </label>
            <div class="form-grid">
              <label>
                波段 1
                <select v-model.number="band1" :disabled="!rendering.can_edit">
                  <option v-for="band in availableBands" :key="band.index" :value="band.index">
                    {{ band.index }} · {{ band.description || band.dtype }}
                  </option>
                </select>
              </label>
              <label v-if="mode === 'RGB'">
                波段 2
                <select v-model.number="band2" :disabled="!rendering.can_edit">
                  <option v-for="band in availableBands" :key="band.index" :value="band.index">
                    {{ band.index }} · {{ band.description || band.dtype }}
                  </option>
                </select>
              </label>
              <label v-if="mode === 'RGB'">
                波段 3
                <select v-model.number="band3" :disabled="!rendering.can_edit">
                  <option v-for="band in availableBands" :key="band.index" :value="band.index">
                    {{ band.index }} · {{ band.description || band.dtype }}
                  </option>
                </select>
              </label>
              <label v-else>
                色带
                <select v-model="colormap" :disabled="!rendering.can_edit">
                  <option v-for="item in rendering.allowed_colormaps" :key="item" :value="item">
                    {{ item }}
                  </option>
                </select>
              </label>
            </div>
            <div class="form-grid">
              <label>
                重采样
                <select v-model="resampling" :disabled="!rendering.can_edit">
                  <option v-for="item in rendering.allowed_resampling" :key="item" :value="item">
                    {{ item }}
                  </option>
                </select>
              </label>
              <label>
                透明度
                <input
                  v-model.number="opacity"
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  :disabled="!rendering.can_edit"
                />
              </label>
            </div>
            <button
              v-if="rendering.can_edit"
              type="button"
              :disabled="saving"
              @click="saveRendering"
            >
              {{ saving ? '保存中…' : '应用渲染' }}
            </button>
          </section>

          <section class="panel-section">
            <div class="section-heading">
              <h2>波段统计</h2>
              <span>修订 {{ rendering.revision }}</span>
            </div>
            <ul class="raster-stats">
              <li v-for="stat in rendering.statistics" :key="stat.band">
                <strong>Band {{ stat.band }}</strong>
                <span>{{ stat.minimum ?? '—' }} – {{ stat.maximum ?? '—' }}</span>
                <small>均值 {{ stat.mean ?? '—' }} · 有效 {{ stat.valid_percent }}%</small>
              </li>
            </ul>
          </section>

          <section class="panel-section feature-section">
            <div class="section-heading">
              <h2>像元查询</h2>
              <span>点击地图</span>
            </div>
            <p v-if="!selectedPixel" class="empty-message">点击地图读取原始波段值。</p>
            <dl v-else class="property-list">
              <template v-for="([key, value]) in selectedEntries" :key="key">
                <dt>{{ key }}</dt>
                <dd>{{ formatValue(value) }}</dd>
              </template>
            </dl>
          </section>
        </template>

        <div v-else class="panel-heading">
          <p class="eyebrow">Raster preview</p>
          <h1>{{ datasetId ? '正在加载栅格' : '未选择栅格数据集' }}</h1>
          <p v-if="!datasetId">在地址中加入 ?rasterDataset=&lt;UUID&gt; 打开预览。</p>
        </div>
        <p v-if="previewError" class="error-message">{{ previewError }}</p>
      </aside>
      <section ref="mapContainer" class="map" aria-label="GeoPortalX raster map" />
    </section>
  </main>
</template>

<style scoped>
.raster-stats {
  display: grid;
  gap: 0.55rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.raster-stats li {
  display: grid;
  gap: 0.15rem;
  padding: 0.65rem;
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 0.7rem;
}

.raster-stats small {
  color: #64748b;
}
</style>
