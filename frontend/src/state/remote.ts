// Conversions between the backend's JSON and the client state, plus the
// localStorage part of the state (cart, checkout marks, import time).
import type {
  ApiCandidate,
  ApiImportPayload,
  ApiObject,
  ApiObjectCreate,
  ApiObjectPatch,
  ApiSpaceCreate,
  ApiSpacePatch,
  ApiStateSnapshot,
} from "@/lib/api";
import type {
  AppState,
  CartLine,
  Dims,
  LegacyAppState,
  LocalState,
  ObjectType,
  Product,
  Space,
  SpaceObject,
  Store,
} from "@/mocks/types";

// ---------------------------------------------------------------------------
// Candidates → products and stores
// ---------------------------------------------------------------------------

const CANDIDATE_PREFIX = "cand_";

export const candidateProductId = (candidateId: number): string => `${CANDIDATE_PREFIX}${candidateId}`;

/** The numeric backend candidate id behind a `cand_<id>` product id. */
export function candidateIdOf(productId: string | undefined): number | undefined {
  if (!productId?.startsWith(CANDIDATE_PREFIX)) return undefined;
  const id = Number(productId.slice(CANDIDATE_PREFIX.length));
  return Number.isInteger(id) && id > 0 ? id : undefined;
}

function hostOf(url: string | null): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).hostname || undefined;
  } catch {
    return undefined;
  }
}

export function candidateStore(c: ApiCandidate): Store {
  const domain = c.seller.domain ?? hostOf(c.seller.url) ?? hostOf(c.url) ?? "unknown-seller";
  return { id: domain, name: c.seller.name ?? domain, domain };
}

function candidateDims(c: ApiCandidate): Partial<Dims> | undefined {
  if (!c.dims) return undefined;
  const out: Partial<Dims> = {};
  for (const key of ["x", "y", "z"] as const) {
    const value = c.dims[key];
    if (typeof value === "number" && value > 0) out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** A search candidate as a Product. `type` is the owning object's type. */
export function candidateToProduct(c: ApiCandidate, type: ObjectType): Product {
  const [first, ...more] = c.materials;
  return {
    id: candidateProductId(c.id),
    title: c.title,
    storeId: candidateStore(c).id,
    price: c.price,
    // The searcher only accepts USD offers.
    currency: "USD",
    dims: candidateDims(c),
    dimsLongest: c.dimsLongest,
    sizeStatus: c.sizeStatus === "fits" ? "fits" : "unverified",
    weight: c.weight,
    type,
    material: first === undefined ? undefined : more.length > 0 ? "mixed" : first,
    imageUrl: c.imageUrl ?? undefined,
    url: c.url ?? undefined,
    checkoutUrl: c.checkoutUrl ?? undefined,
    rating: c.rating,
  };
}

/** Adds candidates (of the given objects) to copies of the product and store maps. */
export function mergeCandidates(
  products: Record<string, Product>,
  stores: Record<string, Store>,
  objects: SpaceObject[],
  candidates: ApiCandidate[],
): { products: Record<string, Product>; stores: Record<string, Store> } {
  const types = new Map(objects.map((o) => [o.id, o.type]));
  const nextProducts = { ...products };
  const nextStores = { ...stores };
  for (const c of candidates) {
    const type = types.get(c.objectId);
    if (!type) continue;
    const product = candidateToProduct(c, type);
    nextProducts[product.id] = product;
    nextStores[product.storeId] ??= candidateStore(c);
  }
  return { products: nextProducts, stores: nextStores };
}

export function withSelectionFirst(candidateIds: string[], selected?: string): string[] {
  if (!selected || !candidateIds.includes(selected)) return candidateIds;
  return [selected, ...candidateIds.filter((id) => id !== selected)];
}

// ---------------------------------------------------------------------------
// Snapshot → state
// ---------------------------------------------------------------------------

/** Drops the backend's legacy mock fields and derives candidateIds and selectedProductId. */
function fromApiObject(o: ApiObject, candidates: ApiCandidate[]): SpaceObject {
  const { selectedCandidateId, candidateIds: _legacyIds, selectedProductId: _legacySelection, ...rest } = o;
  const selected = selectedCandidateId === undefined ? undefined : candidateProductId(selectedCandidateId);
  const ids = candidates.map((c) => candidateProductId(c.id));
  // The newest run found nothing, so the candidates listed are an older run's: show none (keep the pick).
  const visible = rest.search.status === "empty" ? ids.filter((id) => id === selected) : ids;
  return { ...rest, candidateIds: withSelectionFirst(visible, selected), selectedProductId: selected };
}

export function localOf(state: LocalState): LocalState {
  return { cart: state.cart, checkoutOpened: state.checkoutOpened, importedAt: state.importedAt };
}

/** Makes the local part agree with fresh backend data. */
function reconcileLocal(state: AppState): AppState {
  const objects = new Map(state.objects.map((o) => [o.id, o]));
  let changed = false;
  const cart = state.cart.flatMap((line): CartLine[] => {
    const object = objects.get(line.objectId);
    if (!object) {
      changed = true;
      return [];
    }
    // The cart line follows the selection, which may have changed in another tab.
    if (object.source === "search" && object.selectedProductId && object.selectedProductId !== line.productId) {
      changed = true;
      return [{ ...line, productId: object.selectedProductId }];
    }
    return [line];
  });
  let importedAt = state.importedAt;
  if (!importedAt) {
    // Another browser (or a cleared localStorage) imported already.
    const times = state.objects.filter((o) => o.source === "imported").map((o) => o.createdAt);
    importedAt = times.sort()[0];
  }
  if (!changed && importedAt === state.importedAt) return state;
  return { ...state, cart: changed ? cart : state.cart, importedAt };
}

export function snapshotToState(snapshot: ApiStateSnapshot, local: LocalState): AppState {
  const byObject = new Map<string, ApiCandidate[]>();
  for (const c of snapshot.candidates) {
    const list = byObject.get(c.objectId);
    if (list) list.push(c);
    else byObject.set(c.objectId, [c]);
  }
  const objects = snapshot.objects.map((o) => fromApiObject(o, byObject.get(o.id) ?? []));
  const { products, stores } = mergeCandidates({}, {}, objects, snapshot.candidates);
  return reconcileLocal({ version: 1, spaces: snapshot.spaces, objects, products, stores, ...localOf(local) });
}

// ---------------------------------------------------------------------------
// State → request bodies
// ---------------------------------------------------------------------------

/** Structural equality that treats a missing key and an undefined value alike. */
function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  const keys = new Set([...Object.keys(ra), ...Object.keys(rb)]);
  for (const k of keys) if (!sameValue(ra[k], rb[k])) return false;
  return true;
}

/** The changed keys among `keys`, with undefined sent as null (clears the field). */
function diff<T extends object, K extends keyof T>(prev: T, next: T, keys: readonly K[]): Record<string, unknown> | null {
  const body: Record<string, unknown> = {};
  for (const k of keys) {
    if (!sameValue(prev[k], next[k])) body[k as string] = next[k] === undefined ? null : next[k];
  }
  return Object.keys(body).length > 0 ? body : null;
}

const SPACE_PATCH_KEYS = ["name", "type", "unit", "dims", "weightLimit"] as const satisfies readonly (keyof Space)[];

// Never id, createdAt, candidateIds, selectedProductId or search: the backend owns or ignores those.
const OBJECT_PATCH_KEYS = [
  "name",
  "dims",
  "weight",
  "type",
  "material",
  "style",
  "priceLimit",
  "similarItems",
  "source",
  "owned",
] as const satisfies readonly (keyof SpaceObject)[];

export function spaceCreateBody(space: Space): ApiSpaceCreate {
  return {
    id: space.id,
    name: space.name,
    type: space.type,
    unit: space.unit,
    dims: space.dims,
    weightLimit: space.weightLimit,
  };
}

export const spacePatchBody = (prev: Space, next: Space) => diff(prev, next, SPACE_PATCH_KEYS) as ApiSpacePatch | null;

export function objectCreateBody(o: SpaceObject): ApiObjectCreate {
  return {
    id: o.id,
    name: o.name,
    type: o.type,
    dims: o.dims,
    weight: o.weight,
    material: o.material,
    style: o.style,
    priceLimit: o.priceLimit,
    similarItems: o.similarItems,
    source: o.source,
    owned: o.owned,
    createdAt: o.createdAt,
  };
}

export const objectPatchBody = (prev: SpaceObject, next: SpaceObject) =>
  diff(prev, next, OBJECT_PATCH_KEYS) as ApiObjectPatch | null;

// ---------------------------------------------------------------------------
// localStorage
// ---------------------------------------------------------------------------

/** Cart, checkout marks and import time; spaces and objects live in the backend. */
export const LOCAL_KEY = "app-local-v1";
/** The mock-only app's whole state, imported into an empty backend once. */
export const LEGACY_KEY = "wireframe-state-v1";

export const EMPTY_LOCAL: LocalState = { cart: [], checkoutOpened: {} };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function isCartLine(v: unknown): v is CartLine {
  return (
    isRecord(v) &&
    typeof v.id === "string" &&
    typeof v.productId === "string" &&
    typeof v.objectId === "string" &&
    typeof v.qty === "number"
  );
}

function parseJson(raw: string | null): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

export function parseLocal(raw: string | null): LocalState | null {
  const data = parseJson(raw);
  if (!isRecord(data) || !Array.isArray(data.cart) || !isRecord(data.checkoutOpened)) return null;
  const checkoutOpened = Object.fromEntries(
    Object.entries(data.checkoutOpened).filter((e): e is [string, string] => typeof e[1] === "string"),
  );
  return {
    cart: data.cart.filter(isCartLine),
    checkoutOpened,
    importedAt: typeof data.importedAt === "string" ? data.importedAt : undefined,
  };
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readLocal(): LocalState | null {
  try {
    return parseLocal(storage()?.getItem(LOCAL_KEY) ?? null);
  } catch {
    return null;
  }
}

export function writeLocal(local: LocalState): void {
  try {
    storage()?.setItem(LOCAL_KEY, JSON.stringify(localOf(local)));
  } catch {
    // Storage may be unavailable or full; the cart keeps working in memory.
  }
}

export function readLegacy(): LegacyAppState | null {
  let data: unknown;
  try {
    data = parseJson(storage()?.getItem(LEGACY_KEY) ?? null);
  } catch {
    return null;
  }
  if (
    !isRecord(data) ||
    data.version !== 1 ||
    !Array.isArray(data.spaces) ||
    !Array.isArray(data.objects) ||
    !Array.isArray(data.cart) ||
    !isRecord(data.checkoutOpened)
  ) {
    return null;
  }
  return data as unknown as LegacyAppState;
}

export function removeLegacy(): void {
  try {
    storage()?.removeItem(LEGACY_KEY);
  } catch {
    // Ignore unavailable storage.
  }
}

const clip = (text: string, max: number) => (text.length > max ? text.slice(0, max) : text);

/**
 * The old spaces and objects, minus the mock search data (candidates,
 * selection, search state), trimmed to what the backend validates.
 */
export function legacyImportPayload(legacy: LegacyAppState): ApiImportPayload {
  const objects = legacy.objects.map((o): SpaceObject => {
    const { selectedProductId: _mockSelection, ...rest } = o;
    return {
      ...rest,
      name: clip(o.name, 120),
      similarItems: (o.similarItems ?? [])
        .map((s) => s.trim())
        .filter(Boolean)
        .map((s) => clip(s, 80))
        .slice(0, 20),
      candidateIds: [],
      search: { status: "idle" },
    };
  });
  const known = new Set(objects.map((o) => o.id));
  const spaces = legacy.spaces.map((s): Space => {
    const seen = new Set<string>();
    const placements = (s.placements ?? []).filter((p) => {
      if (!known.has(p.objectId) || seen.has(p.objectId)) return false;
      seen.add(p.objectId);
      return true;
    });
    return { ...s, name: clip(s.name, 120), placements };
  });
  return { version: 1, spaces, objects };
}
