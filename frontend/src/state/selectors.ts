import { getProductById, getStoreById } from "@/mocks/catalog";
import type { AppState, CartLine, Product, Space, SpaceObject, Store } from "@/mocks/types";

export function getSpace(state: AppState, id: string): Space | undefined {
  return state.spaces.find((s) => s.id === id);
}

export function getObject(state: AppState, id: string): SpaceObject | undefined {
  return state.objects.find((o) => o.id === id);
}

/** Objects placed in a space, in placement order. */
export function getObjectsForSpace(state: AppState, spaceId: string): SpaceObject[] {
  const space = getSpace(state, spaceId);
  if (!space) return [];
  return space.placements.flatMap((p) => {
    const obj = getObject(state, p.objectId);
    return obj ? [obj] : [];
  });
}

export function getParentSpaces(state: AppState, objectId: string): Space[] {
  return state.spaces.filter((s) => s.placements.some((p) => p.objectId === objectId));
}

export function getSearchableObjects(state: AppState): SpaceObject[] {
  return state.objects.filter((o) => o.source === "search");
}

export function getImportedObjects(state: AppState): SpaceObject[] {
  return state.objects.filter((o) => o.source === "imported");
}

/** Searchable objects that are not placed in any space. */
export function getUnassignedObjects(state: AppState): SpaceObject[] {
  const placed = new Set(state.spaces.flatMap((s) => s.placements.map((p) => p.objectId)));
  return getSearchableObjects(state).filter((o) => !placed.has(o.id));
}

/** Size of an object on the plane of a space, in inches (minimum 1). */
export function footprint(object: SpaceObject, space: Pick<Space, "type">): { w: number; h: number } {
  const w = object.dims.x;
  const h = space.type === "wall" ? (object.dims.z ?? object.dims.y ?? object.dims.x) : (object.dims.y ?? object.dims.x);
  return { w: Math.max(1, w), h: Math.max(1, h) };
}

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function placementRects(state: AppState, space: Space): { objectId: string; rect: Rect }[] {
  return space.placements.flatMap((p) => {
    const obj = getObject(state, p.objectId);
    if (!obj) return [];
    const { w, h } = footprint(obj, space);
    return [{ objectId: obj.id, rect: { x: p.x, y: p.y, w, h } }];
  });
}

/** First free spot on a 6 inch grid, or {0, 0} when nothing is free. */
export function findOpenPosition(
  state: AppState,
  spaceId: string,
  size: { w: number; h: number },
): { x: number; y: number } {
  const space = getSpace(state, spaceId);
  if (!space) return { x: 0, y: 0 };
  const existing = placementRects(state, space).map((r) => r.rect);
  const maxX = Math.max(0, space.dims.x - size.w);
  const maxY = Math.max(0, space.dims.y - size.h);
  const step = 6;
  for (let y = 0; y <= maxY; y += step) {
    for (let x = 0; x <= maxX; x += step) {
      const rect = { x, y, w: size.w, h: size.h };
      if (!existing.some((r) => overlaps(rect, r))) return { x, y };
    }
  }
  return { x: 0, y: 0 };
}

export function getOverlappingObjectIds(state: AppState, spaceId: string): Set<string> {
  const space = getSpace(state, spaceId);
  const result = new Set<string>();
  if (!space) return result;
  const rects = placementRects(state, space);
  for (let i = 0; i < rects.length; i++) {
    for (let j = i + 1; j < rects.length; j++) {
      if (overlaps(rects[i].rect, rects[j].rect)) {
        result.add(rects[i].objectId);
        result.add(rects[j].objectId);
      }
    }
  }
  return result;
}

function selectedProduct(object: SpaceObject): Product | undefined {
  return object.selectedProductId ? getProductById(object.selectedProductId) : undefined;
}

/** Total weight in lb; uses the selected product's weight when the object has none. */
export function getSpaceWeight(state: AppState, spaceId: string): number {
  return getObjectsForSpace(state, spaceId).reduce(
    (sum, o) => sum + (o.weight ?? selectedProduct(o)?.weight ?? 0),
    0,
  );
}

export function getSpaceEstimatedTotal(state: AppState, spaceId: string): number {
  return getObjectsForSpace(state, spaceId).reduce((sum, o) => sum + (selectedProduct(o)?.price ?? 0), 0);
}

export type ObjectStatus =
  | "owned"
  | "not-searched"
  | "searching"
  | "no-matches"
  | "matches"
  | "selected"
  | "in-cart";

export function getCartLineForObject(state: AppState, objectId: string): CartLine | undefined {
  return state.cart.find((l) => l.objectId === objectId);
}

export function getObjectStatus(state: AppState, objectId: string): ObjectStatus {
  const obj = getObject(state, objectId);
  if (!obj) return "not-searched";
  if (obj.source === "imported") return "owned";
  if (getCartLineForObject(state, objectId)) return "in-cart";
  if (obj.search.status === "running") return "searching";
  if (obj.selectedProductId) return "selected";
  if (obj.search.status === "empty") return "no-matches";
  if (obj.candidateIds.length > 0) return "matches";
  return "not-searched";
}

export function getCartCount(state: AppState): number {
  return state.cart.reduce((sum, l) => sum + l.qty, 0);
}

export function getCartTotal(state: AppState): number {
  return state.cart.reduce((sum, l) => sum + l.qty * (getProductById(l.productId)?.price ?? 0), 0);
}

export interface CartStoreGroup {
  store: Store;
  lines: { line: CartLine; product: Product; object?: SpaceObject; spaces: Space[] }[];
  subtotal: number;
  itemCount: number;
  checkoutOpenedAt?: string;
}

export function getCartByStore(state: AppState): CartStoreGroup[] {
  const groups = new Map<string, CartStoreGroup>();
  for (const line of state.cart) {
    const product = getProductById(line.productId);
    const store = product ? getStoreById(product.storeId) : undefined;
    if (!product || !store) continue;
    let group = groups.get(store.id);
    if (!group) {
      group = { store, lines: [], subtotal: 0, itemCount: 0, checkoutOpenedAt: state.checkoutOpened[store.id] };
      groups.set(store.id, group);
    }
    group.lines.push({
      line,
      product,
      object: getObject(state, line.objectId),
      spaces: getParentSpaces(state, line.objectId),
    });
    group.subtotal += product.price * line.qty;
    group.itemCount += line.qty;
  }
  return [...groups.values()].sort((a, b) => a.store.name.localeCompare(b.store.name));
}
