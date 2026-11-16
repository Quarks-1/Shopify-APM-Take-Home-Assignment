// All lengths are stored in inches, weights in lb, prices in USD.
// A space's `unit` is only a display preference.

export type Dims = { x: number; y: number; z?: number }; // x = width, y = depth, z = height (optional)
export type SpaceType = "floor" | "shelf" | "wall" | "other";
export type LengthUnit = "in" | "cm";
export type ObjectType = "furniture" | "lighting" | "rug" | "wall-art" | "storage" | "decor";
export type Material = "metal" | "wood" | "fabric" | "glass" | "ceramic" | "mixed";
export type Style = "modern" | "vintage" | "brutalist" | "scandinavian" | "industrial" | "mid-century";

/** Top-left position in inches, within the space plane. */
export interface Placement {
  objectId: string;
  x: number;
  y: number;
}

export interface Space {
  id: string;
  name: string;
  type: SpaceType;
  unit: LengthUnit;
  dims: Dims;
  weightLimit?: number;
  placements: Placement[];
  createdAt: string;
  updatedAt: string;
}

export type SearchStatus = "idle" | "running" | "done" | "empty";
export type SearchStep = "planning" | "building-query" | "querying-catalog" | "ranking";

export interface SearchState {
  status: SearchStatus;
  step?: SearchStep;
  lastRunAt?: string;
}

export interface OwnedInfo {
  productId: string;
  orderName: string;
  purchasedAt: string;
}

export interface SpaceObject {
  id: string;
  name: string;
  dims: Dims;
  weight?: number;
  type: ObjectType;
  material?: Material;
  style?: Style;
  priceLimit?: number;
  similarItems: string[];
  source: "search" | "imported";
  owned?: OwnedInfo;
  candidateIds: string[];
  selectedProductId?: string;
  search: SearchState;
  createdAt: string;
}

export interface Store {
  id: string;
  name: string;
  domain: string;
}

export interface Product {
  id: string;
  title: string;
  storeId: string;
  price: number;
  currency: "USD";
  dims: Dims;
  weight?: number;
  type: ObjectType;
  material: Material;
  style: Style;
  tags: string[];
}

export interface CartLine {
  id: string;
  productId: string;
  objectId: string;
  qty: number;
}

export interface AppState {
  version: 1;
  spaces: Space[];
  objects: SpaceObject[];
  cart: CartLine[];
  checkoutOpened: Record<string, string>;
  importedAt?: string;
}

export const SPACE_TYPE_OPTIONS: { value: SpaceType; label: string }[] = [
  { value: "floor", label: "Floor" },
  { value: "shelf", label: "Shelf" },
  { value: "wall", label: "Wall" },
  { value: "other", label: "Other" },
];

export const OBJECT_TYPE_OPTIONS: { value: ObjectType; label: string }[] = [
  { value: "furniture", label: "Furniture" },
  { value: "lighting", label: "Lighting" },
  { value: "rug", label: "Rug" },
  { value: "wall-art", label: "Wall art" },
  { value: "storage", label: "Storage" },
  { value: "decor", label: "Decor" },
];

export const MATERIAL_OPTIONS: { value: Material; label: string }[] = [
  { value: "metal", label: "Metal" },
  { value: "wood", label: "Wood" },
  { value: "fabric", label: "Fabric" },
  { value: "glass", label: "Glass" },
  { value: "ceramic", label: "Ceramic" },
  { value: "mixed", label: "Mixed" },
];

export const STYLE_OPTIONS: { value: Style; label: string }[] = [
  { value: "modern", label: "Modern" },
  { value: "vintage", label: "Vintage" },
  { value: "brutalist", label: "Brutalist" },
  { value: "scandinavian", label: "Scandinavian" },
  { value: "industrial", label: "Industrial" },
  { value: "mid-century", label: "Mid-century" },
];

export const UNIT_OPTIONS: { value: LengthUnit; label: string }[] = [
  { value: "in", label: "Inches" },
  { value: "cm", label: "Centimeters" },
];

export const SEARCH_STEP_LABELS: Record<SearchStep, string> = {
  planning: "Searcher agent planning",
  "building-query": "Building catalog query",
  "querying-catalog": "Querying Shopify Global Catalog",
  ranking: "Ranking matches",
};
