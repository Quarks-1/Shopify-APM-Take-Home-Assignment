export interface HealthPayload {
  status: string;
  service: string;
  framework: string;
  database: string;
  debug: boolean;
  timestamp: string;
}

export interface ApiEndpointInfo {
  id: string;
  method: string;
  path: string;
  description: string;
  status: string;
}

export interface OverviewPayload {
  project: string;
  environment: string;
  frontend: string;
  backend: string;
  endpoints: ApiEndpointInfo[];
}

export interface WorkspaceSnapshot {
  health: HealthPayload;
  overview: OverviewPayload;
  latencyMs: number;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchWorkspaceSnapshot(signal?: AbortSignal): Promise<WorkspaceSnapshot> {
  const startedAt = performance.now();
  const [health, overview] = await Promise.all([
    getJson<HealthPayload>("/api/health/", signal),
    getJson<OverviewPayload>("/api/overview/", signal),
  ]);
  const latencyMs = Math.max(1, Math.round(performance.now() - startedAt));
  return { health, overview, latencyMs };
}
