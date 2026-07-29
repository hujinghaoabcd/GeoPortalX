export const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
).replace(/\/$/, '');

export type HealthResponse = {
  status: string;
  service: string;
  database: string;
  timestamp: string;
};

export type VectorLayerSourceResponse = {
  source: {
    type: 'vector';
    tiles: string[];
    minzoom: number;
    maxzoom: number;
    bounds: [number, number, number, number];
  };
  source_layer: string;
  geometry_type: string;
  bounds: [number, number, number, number];
  resource_id: string;
  dataset_id: string;
  layer_id: string;
};

export type MapLibreLayerTemplate = {
  type: 'circle' | 'line' | 'fill';
  paint: Record<string, unknown>;
};

export type VectorStyleField = {
  name: string;
  data_type: string | null;
  distinct_count: number | null;
  minimum: number | null;
  maximum: number | null;
  supports_categorical: boolean;
  supports_graduated: boolean;
};

export type VectorStyleResponse = {
  layer_id: string;
  dataset_id: string;
  resource_id: string;
  layer_title: string;
  resource_title: string;
  geometry_type: string;
  feature_count: number;
  bounds: [number, number, number, number];
  style: {
    id: string;
    mode: 'SIMPLE' | 'CATEGORICAL' | 'GRADUATED';
    field_name: string | null;
    classification_method: 'UNIQUE_VALUES' | 'EQUAL_INTERVAL' | null;
    class_count: number;
    palette: string;
    symbol: Record<string, unknown>;
    classes: Array<Record<string, unknown>>;
    fallback_symbol: Record<string, unknown>;
    revision: number;
    updated_at: string;
  };
  legend: Array<{ label: string; color: string }>;
  maplibre_layers: MapLibreLayerTemplate[];
  fields: VectorStyleField[];
  palettes: Array<{ name: string; colors: string[] }>;
  can_edit: boolean;
};

export type VectorStyleUpdate = {
  mode: 'SIMPLE' | 'CATEGORICAL' | 'GRADUATED';
  field_name: string | null;
  classification_method: 'UNIQUE_VALUES' | 'EQUAL_INTERVAL' | null;
  class_count: number;
  palette: string;
  symbol: Record<string, unknown>;
};

export type VectorIdentifyResponse = {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    id?: number | string;
    geometry: unknown;
    properties: Record<string, unknown>;
  }>;
  selected_fields: string[];
  tolerance_m: number;
  query_point: [number, number];
  layer_id: string;
  dataset_version_id: string;
};

export type RasterSourceResponse = {
  source: {
    type: 'raster';
    tiles: string[];
    tileSize: number;
    minzoom: number;
    maxzoom: number;
    bounds: [number, number, number, number];
  };
  bounds: [number, number, number, number];
  dataset_id: string;
  dataset_version_id: string;
  publication_id: string;
  width: number;
  height: number;
  band_count: number;
  crs: string;
  epsg: number | null;
  opacity: number;
  revision: number;
};

export type RasterBand = {
  index: number;
  dtype: string;
  nodata: number | null;
  description: string | null;
  unit: string | null;
};

export type RasterBandStatistics = {
  band: number;
  minimum: number | null;
  maximum: number | null;
  mean: number | null;
  standard_deviation: number | null;
  percentile_2: number | null;
  percentile_98: number | null;
  valid_percent: number;
};

export type RasterRenderingResponse = {
  dataset_id: string;
  publication_id: string;
  resource_id: string;
  resource_title: string;
  mode: 'SINGLE_BAND' | 'RGB';
  bands: number[];
  rescale: number[][];
  colormap_name: string | null;
  resampling: 'nearest' | 'bilinear' | 'cubic';
  opacity: number;
  revision: number;
  available_bands: RasterBand[];
  statistics: RasterBandStatistics[];
  allowed_colormaps: string[];
  allowed_resampling: string[];
  can_edit: boolean;
};

export type RasterRenderingUpdate = Pick<
  RasterRenderingResponse,
  'mode' | 'bands' | 'rescale' | 'colormap_name' | 'resampling' | 'opacity'
>;

export type RasterPointResponse = {
  dataset_id: string;
  dataset_version_id: string;
  publication_id: string;
  query_point: [number, number];
  result: Record<string, unknown>;
};

export async function fetchHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>(`${apiBaseUrl}/api/v1/health`);
}

export async function fetchVectorLayerSource(
  layerId: string,
): Promise<VectorLayerSourceResponse> {
  return fetchJson<VectorLayerSourceResponse>(
    `${apiBaseUrl}/api/v1/vector-layers/${encodeURIComponent(layerId)}/source`,
  );
}

export async function fetchVectorLayerStyle(
  layerId: string,
): Promise<VectorStyleResponse> {
  return fetchJson<VectorStyleResponse>(
    `${apiBaseUrl}/api/v1/vector-layers/${encodeURIComponent(layerId)}/style`,
  );
}

export async function updateVectorLayerStyle(
  layerId: string,
  payload: VectorStyleUpdate,
): Promise<VectorStyleResponse> {
  return fetchJson<VectorStyleResponse>(
    `${apiBaseUrl}/api/v1/vector-layers/${encodeURIComponent(layerId)}/style`,
    mutationInit('PUT', payload),
  );
}

export async function identifyVectorLayer(
  layerId: string,
  longitude: number,
  latitude: number,
  toleranceMeters = 35,
): Promise<VectorIdentifyResponse> {
  const query = new URLSearchParams({
    longitude: String(longitude),
    latitude: String(latitude),
    tolerance_m: String(toleranceMeters),
    limit: '1',
  });
  return fetchJson<VectorIdentifyResponse>(
    `${apiBaseUrl}/api/v1/vector-layers/${encodeURIComponent(layerId)}/identify?${query}`,
  );
}

export async function fetchRasterSource(datasetId: string): Promise<RasterSourceResponse> {
  return fetchJson<RasterSourceResponse>(
    `${apiBaseUrl}/api/v1/raster-datasets/${encodeURIComponent(datasetId)}/source`,
  );
}

export async function fetchRasterRendering(
  datasetId: string,
): Promise<RasterRenderingResponse> {
  return fetchJson<RasterRenderingResponse>(
    `${apiBaseUrl}/api/v1/raster-datasets/${encodeURIComponent(datasetId)}/rendering`,
  );
}

export async function updateRasterRendering(
  datasetId: string,
  payload: RasterRenderingUpdate,
): Promise<RasterRenderingResponse> {
  return fetchJson<RasterRenderingResponse>(
    `${apiBaseUrl}/api/v1/raster-datasets/${encodeURIComponent(datasetId)}/rendering`,
    mutationInit('PUT', payload),
  );
}

export async function identifyRasterDataset(
  datasetId: string,
  longitude: number,
  latitude: number,
): Promise<RasterPointResponse> {
  const query = new URLSearchParams({
    longitude: String(longitude),
    latitude: String(latitude),
  });
  return fetchJson<RasterPointResponse>(
    `${apiBaseUrl}/api/v1/raster-datasets/${encodeURIComponent(datasetId)}/point?${query}`,
  );
}

export function isGeoPortalApiUrl(url: string): boolean {
  return url.startsWith(`${apiBaseUrl}/api/`);
}

async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...init, credentials: 'include' });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

function mutationInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': readCookie('csrftoken') ?? '',
    },
    body: JSON.stringify(payload),
  };
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const item of document.cookie.split(';')) {
    const value = item.trim();
    if (value.startsWith(prefix)) {
      return decodeURIComponent(value.slice(prefix.length));
    }
  }
  return null;
}
