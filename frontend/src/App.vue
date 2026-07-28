<script setup lang="ts">
import type { Map, MapMouseEvent } from 'maplibre-gl';
import { Popup } from 'maplibre-gl';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import {
  fetchHealth,
  fetchVectorLayerSource,
  fetchVectorLayerStyle,
  identifyVectorLayer,
  updateVectorLayerStyle,
  type VectorIdentifyResponse,
  type VectorLayerSourceResponse,
  type VectorStyleResponse,
  type VectorStyleUpdate,
} from './api/client';
import { createMap } from './map/createMap';
import {
  addVectorLayerPreview,
  removeVectorLayerPreview,
  type PreviewHandles,
} from './map/vectorPreview';

const mapContainer = ref<HTMLElement | null>(null);
const backendStatus = ref('checking');
const previewStatus = ref('idle');
const previewError = ref('');
const styleSaving = ref(false);
const descriptor = ref<VectorLayerSourceResponse | null>(null);
const styleDescriptor = ref<VectorStyleResponse | null>(null);
const selectedFeature = ref<VectorIdentifyResponse['features'][number] | null>(null);
const previewLayerId = new URLSearchParams(window.location.search).get('vectorLayer');

const styleMode = ref<VectorStyleUpdate['mode']>('SIMPLE');
const styleField = ref('');
const styleClassCount = ref(5);
const stylePalette = ref('BLUES');
const symbolColor = ref('#2563eb');
const symbolOpacity = ref(0.8);

let map: Map | undefined;
let previewHandles: PreviewHandles | undefined;
let popup: Popup | undefined;
let clickHandler: ((event: MapMouseEvent) => void) | undefined;

const availableFields = computed(() => {
  const fields = styleDescriptor.value?.fields ?? [];
  if (styleMode.value === 'CATEGORICAL') {
    return fields.filter((field) => field.supports_categorical);
  }
  if (styleMode.value === 'GRADUATED') {
    return fields.filter((field) => field.supports_graduated);
  }
  return fields;
});

const selectedProperties = computed(() => {
  const properties = selectedFeature.value?.properties ?? {};
  return Object.entries(properties).filter(([, value]) => value !== null);
});

watch(styleMode, (mode) => {
  if (mode === 'SIMPLE') {
    styleField.value = '';
    return;
  }
  if (!availableFields.value.some((field) => field.name === styleField.value)) {
    styleField.value = availableFields.value[0]?.name ?? '';
  }
});

onMounted(async () => {
  if (mapContainer.value) {
    map = createMap(mapContainer.value);
    map.once('load', async () => {
      if (!map || !previewLayerId) {
        return;
      }
      await loadPreview(previewLayerId);
      clickHandler = (event) => {
        void identifyAt(event);
      };
      map.on('click', clickHandler);
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
  if (map && clickHandler) {
    map.off('click', clickHandler);
  }
  popup?.remove();
  if (map && previewHandles) {
    removeVectorLayerPreview(map, previewHandles);
  }
  map?.remove();
});

async function loadPreview(layerId: string): Promise<void> {
  if (!map) {
    return;
  }
  previewStatus.value = 'loading';
  previewError.value = '';
  try {
    const [sourceResponse, styleResponse] = await Promise.all([
      fetchVectorLayerSource(layerId),
      fetchVectorLayerStyle(layerId),
    ]);
    descriptor.value = sourceResponse;
    styleDescriptor.value = styleResponse;
    syncStyleForm(styleResponse);
    previewHandles = addVectorLayerPreview(map, sourceResponse, styleResponse);
    previewStatus.value = 'loaded';
  } catch (error) {
    previewStatus.value = 'unavailable';
    previewError.value = error instanceof Error ? error.message : '预览不可用';
  }
}

async function saveStyle(): Promise<void> {
  if (!previewLayerId || !descriptor.value || !styleDescriptor.value || !map) {
    return;
  }
  styleSaving.value = true;
  previewError.value = '';
  try {
    const updated = await updateVectorLayerStyle(previewLayerId, {
      mode: styleMode.value,
      field_name: styleMode.value === 'SIMPLE' ? null : styleField.value,
      classification_method:
        styleMode.value === 'CATEGORICAL'
          ? 'UNIQUE_VALUES'
          : styleMode.value === 'GRADUATED'
            ? 'EQUAL_INTERVAL'
            : null,
      class_count: styleMode.value === 'SIMPLE' ? 1 : styleClassCount.value,
      palette: stylePalette.value,
      symbol: {
        ...styleDescriptor.value.style.symbol,
        color: symbolColor.value,
        opacity: symbolOpacity.value,
      },
    });
    styleDescriptor.value = updated;
    syncStyleForm(updated);
    if (previewHandles) {
      removeVectorLayerPreview(map, previewHandles);
    }
    previewHandles = addVectorLayerPreview(map, descriptor.value, updated);
  } catch (error) {
    previewError.value = error instanceof Error ? error.message : '样式保存失败';
  } finally {
    styleSaving.value = false;
  }
}

async function identifyAt(event: MapMouseEvent): Promise<void> {
  if (!previewLayerId || previewStatus.value !== 'loaded') {
    return;
  }
  try {
    const result = await identifyVectorLayer(
      previewLayerId,
      event.lngLat.lng,
      event.lngLat.lat,
      35,
    );
    selectedFeature.value = result.features[0] ?? null;
    popup?.remove();
    if (selectedFeature.value && map) {
      popup = new Popup({ closeButton: true, maxWidth: '360px' })
        .setLngLat(event.lngLat)
        .setDOMContent(buildPopupContent(selectedFeature.value.properties))
        .addTo(map);
    }
  } catch {
    selectedFeature.value = null;
  }
}

function syncStyleForm(response: VectorStyleResponse): void {
  styleMode.value = response.style.mode;
  styleField.value = response.style.field_name ?? '';
  styleClassCount.value = response.style.class_count;
  stylePalette.value = response.style.palette;
  symbolColor.value = String(response.style.symbol.color ?? '#2563eb');
  symbolOpacity.value = Number(response.style.symbol.opacity ?? 0.8);
}

function buildPopupContent(properties: Record<string, unknown>): HTMLElement {
  const container = document.createElement('div');
  container.className = 'feature-popup';
  const title = document.createElement('strong');
  title.textContent = styleDescriptor.value?.layer_title ?? '要素属性';
  container.append(title);
  const list = document.createElement('dl');
  for (const [key, value] of Object.entries(properties).slice(0, 12)) {
    const term = document.createElement('dt');
    term.textContent = key;
    const description = document.createElement('dd');
    description.textContent = formatValue(value);
    list.append(term, description);
  }
  container.append(list);
  return container;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
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

    <section class="workspace">
      <aside class="preview-panel">
        <template v-if="styleDescriptor">
          <div class="panel-heading">
            <p class="eyebrow">{{ styleDescriptor.resource_title }}</p>
            <h1>{{ styleDescriptor.layer_title }}</h1>
            <p>
              {{ styleDescriptor.geometry_type }} ·
              {{ styleDescriptor.feature_count.toLocaleString('zh-CN') }} 个要素
            </p>
          </div>

          <section class="panel-section">
            <div class="section-heading">
              <h2>图例</h2>
              <span>修订 {{ styleDescriptor.style.revision }}</span>
            </div>
            <ul class="legend-list">
              <li v-for="item in styleDescriptor.legend" :key="`${item.label}-${item.color}`">
                <span class="legend-swatch" :style="{ backgroundColor: item.color }" />
                <span>{{ item.label }}</span>
              </li>
            </ul>
          </section>

          <section class="panel-section">
            <div class="section-heading">
              <h2>样式</h2>
              <span v-if="!styleDescriptor.can_edit">只读</span>
            </div>
            <label>
              渲染方式
              <select v-model="styleMode" :disabled="!styleDescriptor.can_edit">
                <option value="SIMPLE">单一符号</option>
                <option value="CATEGORICAL">分类值</option>
                <option value="GRADUATED">数值分级</option>
              </select>
            </label>
            <label v-if="styleMode !== 'SIMPLE'">
              分类字段
              <select v-model="styleField" :disabled="!styleDescriptor.can_edit">
                <option v-for="field in availableFields" :key="field.name" :value="field.name">
                  {{ field.name }}
                </option>
              </select>
            </label>
            <div class="form-grid">
              <label>
                主色
                <input v-model="symbolColor" type="color" :disabled="!styleDescriptor.can_edit" />
              </label>
              <label>
                透明度
                <input
                  v-model.number="symbolOpacity"
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  :disabled="!styleDescriptor.can_edit"
                />
              </label>
            </div>
            <div v-if="styleMode !== 'SIMPLE'" class="form-grid">
              <label>
                类别数
                <input
                  v-model.number="styleClassCount"
                  type="number"
                  min="1"
                  max="9"
                  :disabled="!styleDescriptor.can_edit"
                />
              </label>
              <label>
                调色板
                <select v-model="stylePalette" :disabled="!styleDescriptor.can_edit">
                  <option
                    v-for="palette in styleDescriptor.palettes"
                    :key="palette.name"
                    :value="palette.name"
                  >
                    {{ palette.name }}
                  </option>
                </select>
              </label>
            </div>
            <button
              v-if="styleDescriptor.can_edit"
              type="button"
              :disabled="styleSaving || (styleMode !== 'SIMPLE' && !styleField)"
              @click="saveStyle"
            >
              {{ styleSaving ? '保存中…' : '应用样式' }}
            </button>
          </section>

          <section class="panel-section feature-section">
            <div class="section-heading">
              <h2>要素识别</h2>
              <span>点击地图</span>
            </div>
            <p v-if="!selectedFeature" class="empty-message">点击地图查看最近要素属性。</p>
            <dl v-else class="property-list">
              <template v-for="([key, value]) in selectedProperties" :key="key">
                <dt>{{ key }}</dt>
                <dd>{{ formatValue(value) }}</dd>
              </template>
            </dl>
          </section>
        </template>

        <div v-else class="panel-heading">
          <p class="eyebrow">Vector preview</p>
          <h1>{{ previewLayerId ? '正在加载图层' : '未选择矢量图层' }}</h1>
          <p v-if="!previewLayerId">在地址中加入 ?vectorLayer=&lt;UUID&gt; 打开数据集预览。</p>
        </div>

        <p v-if="previewError" class="error-message">{{ previewError }}</p>
      </aside>
      <section ref="mapContainer" class="map" aria-label="GeoPortalX map" />
    </section>
  </main>
</template>
