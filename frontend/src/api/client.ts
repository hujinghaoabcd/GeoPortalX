const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export type HealthResponse = {
  status: string;
  service: string;
  database: string;
  timestamp: string;
};

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health`);
  if (!response.ok) {
    throw new Error(`Health request failed: ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}
