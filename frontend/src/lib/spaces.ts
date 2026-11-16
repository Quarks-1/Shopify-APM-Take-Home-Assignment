export type DimensionUnit = "ft" | "m";
export type SpaceType = "floor" | "shelf" | "wall";
export type ObjectType = "furniture" | "poster";
export type Material = "metal" | "wood" | "fabric";
export type Style = "modern" | "vintage" | "brutalist";

export interface SimilarItemRecord {
  id: number;
  name: string;
  image: string;
}

export interface PlacedObject {
  id: number;
  x: string;
  y: string;
  z: string | null;
  weight: string;
  object_type: ObjectType;
  material: Material;
  style: Style;
  price_limit: string;
  similar_items: SimilarItemRecord[];
  space_ids: number[];
}

export interface SpaceSummary {
  id: number;
  x: string;
  y: string;
  z: string | null;
  dimension_unit: DimensionUnit;
  space_type: SpaceType;
  weight_limit: string | null;
  editing_grid_increment_inches: string;
  label: string;
  object_count?: number;
}

export interface SpaceObjectsPayload {
  space: SpaceSummary;
  objects: PlacedObject[];
  spaces: SpaceSummary[];
}

export interface ObjectUpdate {
  x: string;
  y: string;
  z: string | null;
  weight: string;
  object_type: ObjectType;
  material: Material;
  style: Style;
  price_limit: string;
  similar_items: Array<{ id?: number; name: string; image: string }>;
  space_ids: number[];
  editing_space_id: number;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      error?: string;
      errors?: Record<string, string[]>;
    };
    if (body.errors) {
      return Object.entries(body.errors)
        .flatMap(([field, messages]) => messages.map((message) => `${field}: ${message}`))
        .join(" ");
    }
    if (body.error) return body.error;
  } catch {
    // The body was not JSON.
  }
  return `Request failed with HTTP ${response.status}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as T;
}

export function fetchSpaces(signal?: AbortSignal): Promise<{ spaces: SpaceSummary[] }> {
  return requestJson("/api/spaces/", { signal });
}

export function fetchSpaceObjects(spaceId: number, signal?: AbortSignal): Promise<SpaceObjectsPayload> {
  return requestJson(`/api/spaces/${spaceId}/objects/`, { signal });
}

export function updateObject(objectId: number, payload: ObjectUpdate): Promise<PlacedObject> {
  return requestJson(`/api/objects/${objectId}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
