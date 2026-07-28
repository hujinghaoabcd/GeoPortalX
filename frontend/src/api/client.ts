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

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health`, {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(`Health request failed: ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

export async function fetchVectorLayerSource(
  layerId: string,
): Promise<VectorLayerSourceResponse> {
  const response = await fetch(
    `${apiBaseUrl}/api/v1/vector-layers/${encodeURIComponent(layerId)}/source`,
    { credentials: 'include' },
  );
  if (!response.ok) {
    throw new Error(`Vector source request failed: ${response.status}`);
  }
  return (await response.json()) as VectorLayerSourceResponse;
}

export function isGeoPortalApiUrl(url: string): boolean {
  return url.startsWith(`${apiBaseUrl}/api/`);
}
