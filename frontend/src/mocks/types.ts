// All lengths are stored in inches, weights in lb, prices in USD.
// A space's `unit` is only a display preference.

export type Dims = { x: number; y: number; z?: number }; // x = width, y = depth, z = height (optional)
export type ObjectDims = { x: number; y?: number; z?: number }; // x = width, y = depth (optional on walls), z = height (optional)
export type SpaceType = "floor" | "shelf" | "wall" | "other";
export type LengthUnit = "in" | "ft" | "cm";
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
  dims: ObjectDims;
  weight?: number;
  type: ObjectType;
  material?: Material;
  style?: Style;
  /** Lowest acceptable price in USD; undefined means no minimum. */
  priceMin?: number;
  /** Highest acceptable price in USD; undefined means no maximum. */
  priceLimit?: number;
  /** Minimum product star rating (0-5) to accept; unset means any. */
  minRating?: number;
  similarItems: string[];
  source: "search" | "imported";
  owned?: OwnedInfo;
  /** Product ids of the latest search's accepted candidates (`cand_<id>`), selection first. Derived client-side. */
  candidateIds: string[];
  /** Product id of the selected candidate (`cand_<id>`). Derived from the backend's selectedCandidateId. */
  selectedProductId?: string;
  search: SearchState;
  createdAt: string;
}

export interface Store {
  id: string;
  name: string;
  domain: string;
}

export type SizeStatus = "fits" | "unverified";

export interface ProductRating {
  value: number;
  count: number | null;
}

/**
 * Either a real search candidate (id `cand_<candidate id>`, store id = seller
 * domain) or a mock owned product from `mocks/catalog.ts` (id `prd_…`).
 */
export interface Product {
  id: string;
  title: string;
  storeId: string;
  price: number;
  currency: "USD";
  /** Inches. Candidates may know only some sides, or none. */
  dims?: Partial<Dims>;
  /** Longest side in inches, when the catalog only gave one size. */
  dimsLongest?: number;
  /** Whether the size was verified to fit the object (candidates only). */
  sizeStatus?: SizeStatus;
  weight?: number;
  /** For candidates, the owning object's type. */
  type: ObjectType;
  material?: Material;
  style?: Style;
  tags?: string[];
  imageUrl?: string;
  /** Product page. */
  url?: string;
  checkoutUrl?: string;
  rating?: ProductRating;
}

export interface CartLine {
  id: string;
  productId: string;
  objectId: string;
  qty: number;
}

/** The part of the state kept in this browser's localStorage. */
export interface LocalState {
  cart: CartLine[];
  checkoutOpened: Record<string, string>;
  importedAt?: string;
}

/** In-memory cache of the backend (spaces, objects, candidates) plus the local part. */
export interface AppState extends LocalState {
  version: 1;
  spaces: Space[];
  objects: SpaceObject[];
  /** Search candidates as products, keyed by product id (`cand_<id>`). */
  products: Record<string, Product>;
  /** Stores of those candidates, keyed by seller domain. */
  stores: Record<string, Store>;
}

/** What the mock-only app kept under localStorage "wireframe-state-v1". */
export interface LegacyAppState extends LocalState {
  version: 1;
  spaces: Space[];
  objects: SpaceObject[];
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
  { value: "ft", label: "Feet" },
  { value: "cm", label: "Centimeters" },
];

export const SEARCH_STEP_LABELS: Record<SearchStep, string> = {
  planning: "Searcher agent planning",
  "building-query": "Building catalog query",
  "querying-catalog": "Querying Shopify Global Catalog",
  ranking: "Ranking matches",
};
