import type {
  Dims,
  LengthUnit,
  Material,
  ObjectDims,
  ObjectType,
  OwnedInfo,
  SearchStep,
  Space,
  SpaceObject,
  SpaceType,
  Style,
} from "@/mocks/types";

// ---------------------------------------------------------------------------
// Backend status page
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Request helper
// ---------------------------------------------------------------------------

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

/** A failed API call. `status` is 0 when the request never got an HTTP response. */
export class ApiError extends Error {
  readonly status: number;
  /** The backend's `error` code, or `network_error`, `backend_unreachable`, `http_<status>`. */
  readonly code: string;
  /** Field path → message, for validation errors. */
  readonly details?: Record<string, string>;

  constructor(status: number, code: string, message: string, details?: Record<string, string>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** The backend is down or the dev proxy could not reach it. */
  get isUnreachable(): boolean {
    return this.code === "network_error" || this.code === "backend_unreachable";
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** One readable line for a toast or an error panel. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isUnreachable) return "The backend didn't respond.";
    const details = error.details ? Object.entries(error.details).map(([k, v]) => `${k} ${v}`) : [];
    return details.length > 0 ? `${error.code}: ${details.slice(0, 3).join("; ")}` : error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

function stringRecord(value: unknown): Record<string, string> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, String(v)]));
}

export interface RequestOptions {
  body?: unknown;
  signal?: AbortSignal;
}

/** Sends `body` as JSON and parses the JSON reply; non-2xx replies throw an ApiError. */
export async function request<T>(method: string, path: string, { body, signal }: RequestOptions = {}): Promise<T> {
  const label = `${method} ${path}`;
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError(0, "network_error", `${label} failed: the backend is unreachable`);
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = undefined;
  }

  if (!response.ok) {
    const payload = typeof data === "object" && data !== null ? (data as { error?: unknown; details?: unknown }) : {};
    const code =
      typeof payload.error === "string"
        ? payload.error
        : // The Vite proxy answers an empty 5xx when Django is not running.
          response.status >= 500 && !text
          ? "backend_unreachable"
          : `http_${response.status}`;
    throw new ApiError(response.status, code, `${label} failed with HTTP ${response.status} (${code})`, stringRecord(payload.details));
  }
  return data as T;
}

const seg = encodeURIComponent;

export async function fetchWorkspaceSnapshot(signal?: AbortSignal): Promise<WorkspaceSnapshot> {
  const startedAt = performance.now();
  const [health, overview] = await Promise.all([
    request<HealthPayload>("GET", "/api/health/", { signal }),
    request<OverviewPayload>("GET", "/api/overview/", { signal }),
  ]);
  const latencyMs = Math.max(1, Math.round(performance.now() - startedAt));
  return { health, overview, latencyMs };
}

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

/**
 * An object as the backend returns it. `candidateIds` and `selectedProductId`
 * are legacy mock fields the backend still stores: ignore what it returns.
 */
export interface ApiObject extends SpaceObject {
  /** Read-only; changes only through `selectCandidate`. */
  selectedCandidateId?: number;
}

export type ApiSizeStatus = "fits" | "unverified" | "too_big";
export type ApiWeightStatus = "fits" | "unverified" | "too_heavy";
export type ApiVerdict = "pending" | "accepted" | "rejected";

export interface ApiCandidate {
  id: number;
  objectId: string;
  runId: string;
  productId: string;
  variantId: string | null;
  title: string;
  seller: { name: string | null; domain: string | null; url: string | null };
  /** USD dollars. */
  price: number;
  currency: string;
  /** Product page. */
  url: string | null;
  checkoutUrl: string | null;
  imageUrl: string | null;
  rating?: { value: number; count: number | null };
  /** Inches; any subset of x (width), y (depth), z (height). */
  dims?: { x?: number; y?: number; z?: number };
  dimsLongest?: number;
  sizeStatus: ApiSizeStatus;
  /** lb. */
  weight?: number;
  weightStatus: ApiWeightStatus;
  materials: Material[];
  verdict: ApiVerdict;
  verdictReason: string | null;
  filterReasons: string[];
  /** Round of the query that found it. */
  query: number;
}

export type ApiRunStatus = "queued" | "running" | "done" | "empty" | "failed" | "canceled";

export interface ApiSearchQuery {
  round: number;
  strategy: string;
  query: string;
  returned: number;
  new: number;
  passing: number;
  error: string | null;
}

export interface ApiSearchRun {
  id: string;
  objectId: string;
  status: ApiRunStatus;
  step: SearchStep | null;
  round: number;
  maxRounds: number;
  accepted: number;
  target: number;
  partial: boolean;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
  agent: {
    mode: string | null;
    model: string | null;
    turns: number | null;
    sessionId: string | null;
    outcome: string | null;
    notes: string | null;
    fallbackReason: string | null;
    deniedCommands: string[];
  };
  queries: ApiSearchQuery[];
  /** Accepted candidates, best first; with `includeAll`, every candidate. */
  candidates: ApiCandidate[];
}

export const ACTIVE_RUN_STATUSES: ReadonlySet<ApiRunStatus> = new Set(["queued", "running"]);

export interface ApiStateSnapshot {
  spaces: Space[];
  objects: ApiObject[];
  /** Per object: the newest run's accepted candidates, plus its selected one. */
  candidates: ApiCandidate[];
}

/** Body of `POST /api/state/import/`: upserts by id; keys other than these are ignored. */
export interface ApiImportPayload {
  version: 1;
  spaces: Space[];
  objects: SpaceObject[];
}

export interface ApiImportResult {
  imported: { spaces: number; objects: number; placements: number };
  ignored: string[];
}

export interface ApiSpaceCreate {
  id?: string;
  name: string;
  type: SpaceType;
  unit: LengthUnit;
  dims: Dims;
  weightLimit?: number | null;
}

export type ApiSpacePatch = Partial<Omit<ApiSpaceCreate, "id">>;

export interface ApiObjectCreate {
  id?: string;
  name: string;
  type: ObjectType;
  dims: ObjectDims;
  weight?: number | null;
  material?: Material | null;
  style?: Style | null;
  priceMin?: number | null;
  priceLimit?: number | null;
  similarItems?: string[];
  source?: SpaceObject["source"];
  owned?: OwnedInfo | null;
  createdAt?: string;
}

/** `null` clears an optional field. */
export type ApiObjectPatch = Partial<Omit<ApiObjectCreate, "id" | "createdAt">>;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export const getState = (signal?: AbortSignal) => request<ApiStateSnapshot>("GET", "/api/state/", { signal });

export const importState = (payload: ApiImportPayload) =>
  request<ApiImportResult>("POST", "/api/state/import/", { body: payload });

// ---------------------------------------------------------------------------
// Spaces and placements
// ---------------------------------------------------------------------------

export const listSpaces = (signal?: AbortSignal) => request<Space[]>("GET", "/api/spaces/", { signal });

export const createSpace = (body: ApiSpaceCreate) => request<Space>("POST", "/api/spaces/", { body });

export const getSpace = (id: string, signal?: AbortSignal) =>
  request<Space>("GET", `/api/spaces/${seg(id)}/`, { signal });

export const updateSpace = (id: string, patch: ApiSpacePatch) =>
  request<Space>("PATCH", `/api/spaces/${seg(id)}/`, { body: patch });

export const deleteSpace = (id: string) => request<void>("DELETE", `/api/spaces/${seg(id)}/`);

export const putPlacement = (spaceId: string, objectId: string, pos: { x: number; y: number }) =>
  request<Space>("PUT", `/api/spaces/${seg(spaceId)}/placements/${seg(objectId)}/`, { body: { x: pos.x, y: pos.y } });

export const deletePlacement = (spaceId: string, objectId: string) =>
  request<Space>("DELETE", `/api/spaces/${seg(spaceId)}/placements/${seg(objectId)}/`);

// ---------------------------------------------------------------------------
// Objects
// ---------------------------------------------------------------------------

export const listObjects = (signal?: AbortSignal) => request<ApiObject[]>("GET", "/api/objects/", { signal });

export const createObject = (body: ApiObjectCreate) => request<ApiObject>("POST", "/api/objects/", { body });

export const getObject = (id: string, signal?: AbortSignal) =>
  request<ApiObject>("GET", `/api/objects/${seg(id)}/`, { signal });

export const updateObject = (id: string, patch: ApiObjectPatch) =>
  request<ApiObject>("PATCH", `/api/objects/${seg(id)}/`, { body: patch });

export const deleteObject = (id: string) => request<void>("DELETE", `/api/objects/${seg(id)}/`);

// ---------------------------------------------------------------------------
// Searches
// ---------------------------------------------------------------------------

interface RunReadOptions {
  /** Every candidate with its verdict, not only accepted ones. */
  includeAll?: boolean;
  signal?: AbortSignal;
}

const includeQuery = (includeAll?: boolean) => (includeAll ? "?include=all" : "");

/** Starts a run (HTTP 202), or returns the object's active one (200). Imported objects are refused (400). */
export const startSearch = (objectId: string) =>
  request<ApiSearchRun>("POST", `/api/objects/${seg(objectId)}/searches/`);

export const getSearch = (runId: string, { includeAll, signal }: RunReadOptions = {}) =>
  request<ApiSearchRun>("GET", `/api/searches/${seg(runId)}/${includeQuery(includeAll)}`, { signal });

/** The object's newest run; 404 when it was never searched. */
export const getLatestSearch = (objectId: string, { includeAll, signal }: RunReadOptions = {}) =>
  request<ApiSearchRun>("GET", `/api/objects/${seg(objectId)}/searches/latest/${includeQuery(includeAll)}`, { signal });

export const cancelSearch = (runId: string) => request<ApiSearchRun>("POST", `/api/searches/${seg(runId)}/cancel/`);

/** Selects an accepted candidate of the object; `null` clears the selection. */
export const selectCandidate = (objectId: string, candidateId: number | null) =>
  request<ApiObject>("POST", `/api/objects/${seg(objectId)}/select/`, { body: { candidateId } });
