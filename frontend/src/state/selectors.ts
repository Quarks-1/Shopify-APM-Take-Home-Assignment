import type { ApiCartMode, ApiMerchantCart, ApiMerchantMessage } from "@/lib/api";
import { getMockStoreById, getOwnedProductById } from "@/mocks/catalog";
import type { AppState, CartLine, Product, Space, SpaceObject, Store } from "@/mocks/types";

/** A search candidate from the state, else a mock owned product. */
export function getProduct(state: AppState, id: string): Product | undefined {
  return state.products[id] ?? getOwnedProductById(id);
}

/** A candidate's store (id = seller domain), else a mock store of the owned products. */
export function getStore(state: AppState, id: string): Store | undefined {
  return state.stores[id] ?? getMockStoreById(id);
}

export function getSelectedProduct(state: AppState, object: SpaceObject): Product | undefined {
  return object.selectedProductId ? getProduct(state, object.selectedProductId) : undefined;
}

/** The product that stands for an object: what it is (owned) or what was picked for it. */
export function getObjectProduct(state: AppState, object: SpaceObject): Product | undefined {
  return object.owned ? getProduct(state, object.owned.productId) : getSelectedProduct(state, object);
}

/** The object's candidate products in `candidateIds` order, skipping unknown ids. */
export function getCandidateProducts(state: AppState, object: SpaceObject): Product[] {
  return object.candidateIds.flatMap((id) => {
    const product = getProduct(state, id);
    return product ? [product] : [];
  });
}

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

/** Total weight in lb; uses the selected product's weight when the object has none. */
export function getSpaceWeight(state: AppState, spaceId: string): number {
  return getObjectsForSpace(state, spaceId).reduce(
    (sum, o) => sum + (o.weight ?? getSelectedProduct(state, o)?.weight ?? 0),
    0,
  );
}

export function getSpaceEstimatedTotal(state: AppState, spaceId: string): number {
  return getObjectsForSpace(state, spaceId).reduce((sum, o) => sum + (getSelectedProduct(state, o)?.price ?? 0), 0);
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

/** Lines whose product can't be resolved are skipped, here and in the totals and groups. */
export function getCartCount(state: AppState): number {
  return state.cart.reduce((sum, l) => sum + (getProduct(state, l.productId) ? l.qty : 0), 0);
}

export interface CartLineView {
  line: CartLine;
  product: Product;
  object?: SpaceObject;
  spaces: Space[];
  /** The merchant's disclosures for this line's variant. Always shown, never hidden. */
  disclosures: ApiMerchantMessage[];
  /** Buy this line on the store's site: set when the merchant cart can't hold it. */
  buyUrl?: string;
}

export interface CartStoreGroup {
  store: Store;
  lines: CartLineView[];
  /** Local estimate from product prices, in USD. */
  subtotal: number;
  itemCount: number;
  checkoutOpenedAt?: string;
  /** The backend's merchant cart for the store, once it has synced. */
  merchant?: ApiMerchantCart;
  /** Cart edits or a merchant sync for the store are on their way. */
  syncing: boolean;
  mode: ApiCartMode;
  /** Notices to list under the store header, plus disclosures that match no line. */
  notices: ApiMerchantMessage[];
}

function isHttpsUrl(value: string | undefined | null): value is string {
  if (!value) return false;
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

/** True when the group's merchant cart is current and its totals can be shown as the merchant's. */
export function hasMerchantTotals(group: CartStoreGroup): boolean {
  const { merchant } = group;
  return (
    !group.syncing &&
    merchant !== undefined &&
    merchant.status === "synced" &&
    merchant.mode === "checkout" &&
    merchant.currency !== null &&
    merchant.totals.length > 0
  );
}

/** The merchant's grand total: the `total` entry, else the last one (merchant order). */
export function getMerchantTotal(merchant: ApiMerchantCart): ApiMerchantCart["totals"][number] | undefined {
  return merchant.totals.find((t) => t.type === "total") ?? merchant.totals.at(-1);
}

export type CartTotal =
  | { estimated: false; amountMinor: number; currency: string }
  | { estimated: true; amount: number; currency: "USD" };

/**
 * The merchants' totals summed when every store has synced in one currency,
 * else the estimate from local product prices.
 */
export function getCartTotal(state: AppState): CartTotal {
  const groups = getCartByStore(state);
  const estimate: CartTotal = {
    estimated: true,
    amount: state.cart.reduce((sum, l) => sum + l.qty * (getProduct(state, l.productId)?.price ?? 0), 0),
    currency: "USD",
  };
  if (groups.length === 0 || !groups.every(hasMerchantTotals)) return estimate;
  const currencies = new Set(groups.map((g) => g.merchant?.currency));
  const [currency] = currencies;
  if (currencies.size !== 1 || !currency) return estimate;
  let amountMinor = 0;
  for (const group of groups) {
    const total = group.merchant ? getMerchantTotal(group.merchant) : undefined;
    if (!total || !Number.isFinite(total.amount)) return estimate;
    amountMinor += total.amount;
  }
  return { estimated: false, amountMinor, currency };
}

export function getCartByStore(state: AppState): CartStoreGroup[] {
  const groups = new Map<string, CartStoreGroup>();
  for (const line of state.cart) {
    const product = getProduct(state, line.productId);
    const store = product ? getStore(state, product.storeId) : undefined;
    if (!product || !store) continue;
    let group = groups.get(store.id);
    if (!group) {
      const merchant = state.merchantCarts[store.id];
      group = {
        store,
        lines: [],
        subtotal: 0,
        itemCount: 0,
        checkoutOpenedAt: merchant?.checkoutOpenedAt ?? undefined,
        merchant,
        syncing: Boolean(state.cartSyncing[store.id]),
        mode: merchant?.mode ?? "checkout",
        notices: [],
      };
      groups.set(store.id, group);
    }
    const { merchant } = group;
    const variantId = product.variantId ?? line.variantId ?? undefined;
    const fallbackUrl = merchant?.fallback.find((f) => f.lineId === line.id)?.url;
    const buyUrl = isHttpsUrl(fallbackUrl)
      ? fallbackUrl
      : group.mode === "links"
        ? [product.checkoutUrl, product.url].find(isHttpsUrl)
        : undefined;
    group.lines.push({
      line,
      product,
      object: getObject(state, line.objectId),
      spaces: getParentSpaces(state, line.objectId),
      disclosures:
        merchant && variantId
          ? merchant.messages.filter((m) => m.presentation === "disclosure" && m.variantId === variantId)
          : [],
      buyUrl,
    });
    group.subtotal += product.price * line.qty;
    group.itemCount += line.qty;
  }
  for (const group of groups.values()) {
    const shown = new Set(group.lines.flatMap((l) => l.disclosures));
    group.notices = (group.merchant?.messages ?? []).filter((m) => m.presentation !== "disclosure" || !shown.has(m));
  }
  return [...groups.values()].sort((a, b) => a.store.name.localeCompare(b.store.name));
}
