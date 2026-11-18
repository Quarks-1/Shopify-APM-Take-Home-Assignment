// The app's state container. The backend owns spaces, objects, placements,
// search state, candidates, selection and the cart; this store is an in-memory
// cache of them plus the local part (import time) kept in localStorage.
// Actions update the cache synchronously, then send the change to the backend
// through a serial write queue (optimistic updates). A failed write shows a
// toast and reloads the cache from GET /api/state/.
//
// Cart edits are fast DB writes and go through the queue too. Each answers
// with the stores whose merchant cart is now out of date (`dirtyStores`); those
// get a merchant sync through the per-store CartSyncScheduler, outside the
// queue, because syncs call the merchant over UCP and are slow.
import { toast } from "@/components/base/toast/toast";
import * as api from "@/lib/api";
import { ApiError, describeError } from "@/lib/api";
import { createImportedObjects, createSeedState, type SeedState } from "@/mocks/seed";
import type {
  AppState,
  CartLine,
  LegacyAppState,
  LocalState,
  ObjectDims,
  ObjectType,
  Placement,
  SearchState,
  Space,
  SpaceObject,
  ProductVote,
} from "@/mocks/types";
import { CartSyncScheduler } from "@/state/cart-sync";
import {
  EMPTY_LOCAL,
  LOCAL_KEY,
  candidateIdOf,
  candidateProductId,
  fromApiCartLine,
  hasLegacyCart,
  legacyImportPayload,
  localOf,
  mergeCandidates,
  objectCreateBody,
  objectPatchBody,
  parseLocal,
  readLegacy,
  readLegacyCart,
  readLocal,
  removeLegacy,
  snapshotToState,
  spaceCreateBody,
  spacePatchBody,
  stripLegacyCart,
  withSelectionFirst,
  writeLocal,
} from "@/state/remote";
import {
  findOpenPosition,
  footprint,
  getCartLineForObject,
  getObject,
  getProduct,
  getSpace,
  usesVerticalPlane,
} from "@/state/selectors";
import { WriteQueue } from "@/state/write-queue";

export type SpaceInput = Omit<Space, "id" | "placements" | "createdAt" | "updatedAt">;
export type SpacePatch = Partial<Omit<Space, "id" | "placements">>;
export type ObjectPatch = Partial<Omit<SpaceObject, "id">>;
type Pos = { x: number; y: number };

/** Keyboard nudges and drags send only their last value, this long after the last change. */
const COALESCE_MS = 400;
/** Coming back to the tab refreshes from the backend, at most this often. */
const REFRESH_ON_FOCUS_MS = 2000;
const SAVE_TOAST_ID = "backend-save-error";
/** Stores a refresh finds dirty or stale are synced again at most this often. */
const AUTO_SYNC_MIN_GAP_MS = 30_000;
const MAX_LINE_QTY = 20;

export type CheckoutKind = api.ApiCheckoutResult["kind"];

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type Action =
  | { type: "hydrate"; snapshot: api.ApiStateSnapshot }
  | { type: "reset"; seed: SeedState }
  | { type: "local/replace"; local: LocalState }
  | { type: "space/create"; id: string; input: SpaceInput; now: string }
  | { type: "space/update"; id: string; patch: SpacePatch; now: string }
  | { type: "space/set-vibe-board"; id: string; vibeBoardId: string | undefined }
  | { type: "space/delete"; id: string }
  | { type: "placement/place"; spaceId: string; objectId: string; pos: Pos; now: string }
  | { type: "placement/move"; spaceId: string; objectId: string; pos: Pos; now: string }
  | { type: "placement/remove"; spaceId: string; objectId: string; now: string }
  | { type: "placement/restore"; spaceId: string; placement: Placement; now: string }
  | { type: "object/create"; object: SpaceObject }
  | { type: "object/update"; id: string; patch: ObjectPatch }
  | { type: "object/delete"; id: string }
  | { type: "search/set-state"; id: string; search: SearchState }
  | { type: "search/results"; id: string; candidates: api.ApiCandidate[]; lastRunAt: string; keepSelection?: boolean }
  | { type: "product/select"; objectId: string; productId: string | undefined }
  | { type: "votes/set"; objectId: string; votes: Record<string, ProductVote> }
  | { type: "preferences/set"; objectId: string; preferences: SpaceObject["preferences"] }
  | { type: "cart/add"; objectIds: string[]; ids: string[] }
  | { type: "cart/set-qty"; lineId: string; qty: number }
  | { type: "cart/remove"; lineId: string }
  | { type: "cart/clear" }
  | { type: "cart/replace"; lines: CartLine[] }
  | { type: "merchant/syncing"; storeId: string; syncing: boolean }
  | { type: "merchant/synced"; storeId: string; cart: api.ApiMerchantCart }
  | { type: "merchant/error"; storeId: string; error: { code: string; message: string } }
  | { type: "import"; objects: SpaceObject[]; now: string };

function mapSpace(state: AppState, id: string, fn: (space: Space) => Space): AppState {
  return { ...state, spaces: state.spaces.map((s) => (s.id === id ? fn(s) : s)) };
}

function mapObject(state: AppState, id: string, fn: (o: SpaceObject) => SpaceObject): AppState {
  return { ...state, objects: state.objects.map((o) => (o.id === id ? fn(o) : o)) };
}

/** Points the object's line at another product; the store and variant then follow the product. */
function setCartProduct(cart: CartLine[], objectId: string, productId: string): CartLine[] {
  return cart.map((l) =>
    l.objectId === objectId && l.productId !== productId
      ? { ...l, productId, storeId: undefined, variantId: undefined }
      : l,
  );
}

/** A merchant cart row for a store the backend has not described yet. */
function blankMerchantCart(storeId: string): api.ApiMerchantCart {
  return {
    storeId,
    status: "dirty",
    mode: "checkout",
    cartId: null,
    currency: null,
    totals: [],
    messages: [],
    lineItems: [],
    permalink: null,
    fallback: [],
    error: null,
    syncedAt: null,
    checkoutOpenedAt: null,
    stale: true,
  };
}

/** Adds or updates a cart line for an object; returns the same cart if nothing applies. */
function upsertCartLine(state: AppState, objectId: string, newLineId: string): CartLine[] {
  const obj = getObject(state, objectId);
  if (!obj || obj.source !== "search" || !obj.selectedProductId) return state.cart;
  if (state.cart.some((l) => l.objectId === objectId)) {
    return setCartProduct(state.cart, objectId, obj.selectedProductId);
  }
  return [...state.cart, { id: newLineId, productId: obj.selectedProductId, objectId, qty: 1 }];
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "hydrate":
      return snapshotToState(action.snapshot, state);
    case "reset":
      return {
        version: 1,
        spaces: action.seed.spaces,
        objects: action.seed.objects,
        products: {},
        stores: {},
        cart: [],
        merchantCarts: {},
        cartSyncing: state.cartSyncing,
        ...EMPTY_LOCAL,
      };
    case "local/replace":
      return { ...state, ...localOf(action.local) };

    case "space/create":
      return {
        ...state,
        spaces: [
          ...state.spaces,
          { ...action.input, id: action.id, placements: [], createdAt: action.now, updatedAt: action.now },
        ],
      };
    case "space/update":
      return mapSpace(state, action.id, (s) => ({ ...s, ...action.patch, updatedAt: action.now }));
    case "space/set-vibe-board":
      return mapSpace(state, action.id, (s) =>
        s.vibeBoardId === action.vibeBoardId ? s : { ...s, vibeBoardId: action.vibeBoardId },
      );
    case "space/delete":
      return { ...state, spaces: state.spaces.filter((s) => s.id !== action.id) };

    case "placement/place":
      return mapSpace(state, action.spaceId, (s) =>
        s.placements.some((p) => p.objectId === action.objectId)
          ? s
          : { ...s, placements: [...s.placements, { objectId: action.objectId, ...action.pos }], updatedAt: action.now },
      );
    case "placement/move":
      return mapSpace(state, action.spaceId, (s) => ({
        ...s,
        placements: s.placements.map((p) => (p.objectId === action.objectId ? { ...p, ...action.pos } : p)),
        updatedAt: action.now,
      }));
    case "placement/remove":
      return mapSpace(state, action.spaceId, (s) => ({
        ...s,
        placements: s.placements.filter((p) => p.objectId !== action.objectId),
        updatedAt: action.now,
      }));
    case "placement/restore":
      return mapSpace(state, action.spaceId, (s) => ({
        ...s,
        placements: [...s.placements.filter((p) => p.objectId !== action.placement.objectId), action.placement],
        updatedAt: action.now,
      }));

    case "object/create":
      return { ...state, objects: [...state.objects, action.object] };
    case "object/update":
      return mapObject(state, action.id, (o) => ({ ...o, ...action.patch }));
    case "object/delete":
      return {
        ...state,
        objects: state.objects.filter((o) => o.id !== action.id),
        spaces: state.spaces.map((s) => ({ ...s, placements: s.placements.filter((p) => p.objectId !== action.id) })),
        cart: state.cart.filter((l) => l.objectId !== action.id),
      };

    case "search/set-state":
      return mapObject(state, action.id, (o) => ({ ...o, search: action.search }));
    case "search/results": {
      const obj = getObject(state, action.id);
      if (!obj) return state;
      const { products, stores } = mergeCandidates(state.products, state.stores, [obj], action.candidates);
      const found = action.candidates.map((c) => candidateProductId(c.id));
      if (action.keepSelection && found.length === 0 && obj.candidateIds.length > 0) {
        return mapObject({ ...state, products, stores }, action.id, (o) => ({
          ...o,
          search: { status: "done", lastRunAt: action.lastRunAt },
        }));
      }
      const kept = action.keepSelection ? obj.selectedProductId : undefined;
      if (kept) {
        // "More like this" / "Refresh" keeps the source product (first), its cart line and its product/store entries.
        const ids = [kept, ...found.filter((id) => id !== kept)];
        return mapObject({ ...state, products, stores }, action.id, (o) => ({
          ...o,
          candidateIds: ids,
          search: { status: "done", lastRunAt: action.lastRunAt },
        }));
      }
      const ids = found;
      // A fresh search never picks for the user: clear the selection and its cart lines.
      const next: AppState = {
        ...mapObject(state, action.id, (o) => ({
          ...o,
          candidateIds: ids,
          selectedProductId: undefined,
          search: { status: ids.length > 0 ? "done" : "empty", lastRunAt: action.lastRunAt },
        })),
        products,
        stores,
      };
      return obj.selectedProductId ? { ...next, cart: next.cart.filter((l) => l.objectId !== action.id) } : next;
    }

    case "votes/set":
      return mapObject(state, action.objectId, (o) => ({ ...o, votes: action.votes }));

    case "preferences/set":
      return mapObject(state, action.objectId, (o) => ({ ...o, preferences: action.preferences }));

    case "product/select": {
      const next = mapObject(state, action.objectId, (o) => ({
        ...o,
        selectedProductId: action.productId,
        candidateIds: withSelectionFirst(o.candidateIds, action.productId),
      }));
      if (action.productId === undefined) {
        return { ...next, cart: next.cart.filter((l) => l.objectId !== action.objectId) };
      }
      return { ...next, cart: setCartProduct(next.cart, action.objectId, action.productId) };
    }

    case "cart/add": {
      let next = state;
      action.objectIds.forEach((objectId, i) => {
        next = { ...next, cart: upsertCartLine(next, objectId, action.ids[i]) };
      });
      return next;
    }
    case "cart/set-qty":
      return {
        ...state,
        cart:
          action.qty <= 0
            ? state.cart.filter((l) => l.id !== action.lineId)
            : state.cart.map((l) => (l.id === action.lineId ? { ...l, qty: action.qty } : l)),
      };
    case "cart/remove":
      return { ...state, cart: state.cart.filter((l) => l.id !== action.lineId) };
    case "cart/clear":
      return { ...state, cart: [] };
    case "cart/replace":
      return { ...state, cart: action.lines };

    case "merchant/syncing": {
      if (Boolean(state.cartSyncing[action.storeId]) === action.syncing) return state;
      const { [action.storeId]: _done, ...rest } = state.cartSyncing;
      return { ...state, cartSyncing: action.syncing ? { ...rest, [action.storeId]: true } : rest };
    }
    case "merchant/synced": {
      if (action.cart.status === "empty") {
        const { [action.storeId]: _gone, ...rest } = state.merchantCarts;
        return { ...state, merchantCarts: rest };
      }
      return { ...state, merchantCarts: { ...state.merchantCarts, [action.storeId]: action.cart } };
    }
    case "merchant/error": {
      // The sync request itself failed: keep the last snapshot, shown as stale.
      const current = state.merchantCarts[action.storeId] ?? blankMerchantCart(action.storeId);
      return {
        ...state,
        merchantCarts: {
          ...state.merchantCarts,
          [action.storeId]: { ...current, status: "error", error: action.error, stale: true },
        },
      };
    }

    case "import":
      if (state.importedAt) return state;
      return { ...state, objects: [...state.objects, ...action.objects], importedAt: action.now };
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export interface AppActions {
  createSpace(input: SpaceInput): string;
  updateSpace(id: string, patch: SpacePatch): void;
  /** Local only: mirrors `vibeBoardId` attached by a background vibe import worker. */
  setSpaceVibeBoard(id: string, vibeBoardId: string | undefined): void;
  deleteSpace(id: string): void;

  placeObject(spaceId: string, objectId: string, pos?: Pos): void;
  moveObject(spaceId: string, objectId: string, pos: Pos): void;
  removeFromSpace(spaceId: string, objectId: string): void;
  restorePlacement(spaceId: string, placement: Placement): void;

  createObject(input: Partial<SpaceObject> & { name: string; type: ObjectType }): string;
  updateObject(id: string, patch: ObjectPatch): void;
  resizeObject(id: string, footprintSize: { w: number; h: number }, spaceId: string): void;
  deleteObject(id: string): void;

  /** Local only: the backend derives search state from its runs. */
  setSearchState(id: string, search: SearchState): void;
  /** Merges a finished run's accepted candidates and keeps (or auto-picks) the selection. */
  applySearchResults(
    objectId: string,
    candidates: api.ApiCandidate[],
    finishedAt?: string | null,
    opts?: { keepSelection?: boolean },
  ): void;
  /** Local only: what the latest finished run's agent learned (`null` clears it). */
  setPreferences(objectId: string, preferences: SpaceObject["preferences"]): void;
  /** Selects a candidate product on an object, or clears selection (and any cart line) when `undefined`. */
  selectProduct(objectId: string, productId: string | undefined): void;
  /** Thumbs up/down on a candidate (`null` clears the vote). */
  voteProduct(objectId: string, productId: string, vote: ProductVote | null): void;

  addToCart(objectId: string): boolean;
  addAllToCart(objectIds: string[]): number;
  setQty(lineId: string, qty: number): void;
  removeLine(lineId: string): void;
  clearCart(): void;
  /** Syncs the store's merchant cart now, bypassing the backend's freshness and unsupported checks (Retry). */
  syncStore(storeId: string): void;
  /**
   * Makes a fresh checkout for the store (after pending cart writes land) and
   * returns the https URL to open. Rejects with a readable Error when the
   * store can't check out.
   */
  checkout(storeId: string): Promise<{ url: string; kind: CheckoutKind }>;

  importFromShopify(): void;
  resetDemo(): void;
}

export type AppStatus = { phase: "loading" } | { phase: "ready" } | { phase: "error"; error: unknown };

/** What the search provider needs beyond the public actions. */
export interface StoreInternals {
  getState(): AppState;
  /** Runs a backend call after every pending write (so it never overtakes them). */
  enqueue<T>(task: () => Promise<T>): Promise<T>;
  /** Called when the demo is reset and every object is replaced. */
  onReset(listener: () => void): () => void;
  /** Whether a reset's backend work is still pending (backend reads can return pre-reset data until it settles). */
  isResetting(): boolean;
  /** Called once every pending reset has finished on the backend (success or failure); refetch backend data then. */
  onResetSettled(listener: () => void): () => void;
}

export interface AppStore extends StoreInternals {
  subscribe(listener: () => void): () => void;
  getStatus(): AppStatus;
  actions: AppActions;
  retry(): void;
  /** Loads from the backend and starts listening to other tabs; returns the teardown. */
  start(): () => void;
}

function makeId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${random}`;
}

const nowIso = () => new Date().toISOString();

/** Object dims after resizing its footprint on a space's plane (vertical plane: x and z, otherwise x and y). */
function resizedDims(object: SpaceObject, space: Space, { w, h }: { w: number; h: number }): ObjectDims {
  const { dims } = object;
  return usesVerticalPlane(object, space) ? { ...dims, x: w, z: h } : { ...dims, x: w, y: h };
}

/** A delete of something already gone (another tab, a reset) counts as done. */
async function ignoreMissing(task: () => Promise<unknown>): Promise<void> {
  try {
    await task();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}

function legacyLocal(current: LocalState, legacy: LegacyAppState): LocalState {
  // The old cart points at mock products, so it stays behind; the import time carries over.
  const old = parseLocal(JSON.stringify({ importedAt: legacy.importedAt })) ?? EMPTY_LOCAL;
  return { importedAt: current.importedAt ?? old.importedAt };
}

function isHttpsUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function isMerchantCart(value: unknown): value is api.ApiMerchantCart {
  return typeof value === "object" && value !== null && typeof (value as { storeId?: unknown }).storeId === "string";
}

const clampQty = (qty: number) => Math.min(MAX_LINE_QTY, Math.max(0, Math.round(qty)));

/** The store a line belongs to: its product's store, else what the backend said. */
function storeOfLine(state: AppState, line: CartLine): string | undefined {
  return getProduct(state, line.productId)?.storeId ?? line.storeId;
}

function storesOfLines(state: AppState, lines: CartLine[]): string[] {
  return [...new Set(lines.flatMap((l) => storeOfLine(state, l) ?? []))];
}

/** 404 on a line write means another tab removed it already: nothing to sync. */
async function dirtyOrNone(task: () => Promise<{ dirtyStores?: string[] }>): Promise<string[]> {
  try {
    return (await task()).dirtyStores ?? [];
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return [];
    throw error;
  }
}

function checkoutUnavailableMessage(reason: string | undefined): string {
  const detail = reason ? ` (${reason.replaceAll("_", " ")})` : "";
  return `This store can't open a checkout right now${detail}.`;
}

export function createAppStore(): AppStore {
  let state: AppState = {
    version: 1,
    spaces: [],
    objects: [],
    products: {},
    stores: {},
    cart: [],
    merchantCarts: {},
    cartSyncing: {},
    ...(readLocal() ?? EMPTY_LOCAL),
  };
  let status: AppStatus = { phase: "loading" };
  let lastSyncAt = 0;
  let resyncQueued = false;
  let booting: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  const resetListeners = new Set<() => void>();
  const resetSettledListeners = new Set<() => void>();
  let pendingResets = 0;
  const queue = new WriteQueue();

  const emit = () => listeners.forEach((l) => l());

  function dispatch(action: Action, { persist = true }: { persist?: boolean } = {}): void {
    const prev = state;
    const next = reducer(prev, action);
    if (next === prev) return;
    state = next;
    if (persist && next.importedAt !== prev.importedAt) writeLocal(next);
    emit();
  }

  function setStatus(next: AppStatus): void {
    status = next;
    emit();
  }

  function hydrate(snapshot: api.ApiStateSnapshot): void {
    lastSyncAt = Date.now();
    dispatch({ type: "hydrate", snapshot });
    syncStaleStores();
  }

  // -------------------------------------------------------------------------
  // Merchant cart syncs
  // -------------------------------------------------------------------------

  /** Cart writes queued or running: write key → the stores they touch. */
  const pendingCartWrites = new Map<string, { stores: string[]; token: object }>();
  /** When a refresh last synced a store on its own. */
  const lastAutoSync = new Map<string, number>();

  const scheduler = new CartSyncScheduler({
    run: (storeId, force) => api.syncStore(storeId, force),
    onStart: (storeId) => publishSyncing([storeId]),
    onResult(storeId, cart, settled) {
      dispatch({ type: "merchant/synced", storeId, cart });
      if (settled) publishSyncing([storeId]);
    },
    onError(storeId, error, settled) {
      const code = error instanceof ApiError ? error.code : "sync_failed";
      dispatch({ type: "merchant/error", storeId, error: { code, message: describeError(error) } });
      if (settled) publishSyncing([storeId]);
    },
  });

  function hasPendingCartWrite(storeId: string): boolean {
    for (const pending of pendingCartWrites.values()) if (pending.stores.includes(storeId)) return true;
    return false;
  }

  /** Brings `cartSyncing` up to date for the stores. */
  function publishSyncing(storeIds: Iterable<string>): void {
    for (const storeId of storeIds) {
      const syncing = scheduler.isBusy(storeId) || hasPendingCartWrite(storeId);
      dispatch({ type: "merchant/syncing", storeId, syncing });
    }
  }

  function scheduleSync(storeIds: Iterable<string | undefined>, options?: { force?: boolean; delayMs?: number }): void {
    const ids = [...new Set(storeIds)].filter((id): id is string => Boolean(id));
    for (const id of ids) scheduler.schedule(id, options);
    publishSyncing(ids);
  }

  /** Syncs stores the backend reports dirty or stale, and stores with lines but no merchant cart yet. */
  function syncStaleStores(): void {
    const due = new Set<string>();
    for (const merchant of Object.values(state.merchantCarts)) {
      if (merchant.status === "dirty" || merchant.stale) due.add(merchant.storeId);
    }
    for (const line of state.cart) {
      const storeId = storeOfLine(state, line);
      if (storeId && !state.merchantCarts[storeId]) due.add(storeId);
    }
    const now = Date.now();
    const ids = [...due].filter(
      (id) => !scheduler.isBusy(id) && now - (lastAutoSync.get(id) ?? Number.NEGATIVE_INFINITY) >= AUTO_SYNC_MIN_GAP_MS,
    );
    for (const id of ids) lastAutoSync.set(id, now);
    scheduleSync(ids, { delayMs: 0 });
    // Clears marks left behind by writes that failed before this refresh.
    publishSyncing(Object.keys(state.cartSyncing));
  }

  /**
   * Sends a cart write through the queue, marking `stores` as updating until
   * it lands, then syncs the stores it reports dirty. Writes with the same
   * `coalesceKey` send only their last value.
   */
  function writeCart(stores: (string | undefined)[], task: () => Promise<string[]>, coalesceKey?: string): void {
    const touched = [...new Set(stores)].filter((id): id is string => Boolean(id));
    const key = coalesceKey ?? makeId("cart-write");
    const token = {};
    pendingCartWrites.set(key, { stores: touched, token });
    publishSyncing(touched);
    const run = async () => {
      try {
        scheduleSync(await task());
      } finally {
        if (pendingCartWrites.get(key)?.token === token) pendingCartWrites.delete(key);
        publishSyncing(touched);
      }
    };
    if (coalesceKey) writeCoalesced(coalesceKey, run);
    else write(run);
  }

  /** Upserts lines; adopts the backend's lines when no other write is waiting to change them. */
  async function postLines(lines: api.ApiCartLineInput[]): Promise<string[]> {
    const result = await api.addCartLines(lines);
    if (!queue.hasOthersPending()) dispatch({ type: "cart/replace", lines: result.lines.map(fromApiCartLine) });
    return result.dirtyStores ?? [];
  }

  /**
   * Posts the cart older builds kept in localStorage, once: only when the
   * backend's cart is still empty (the POST is idempotent by object anyway).
   * Returns true when the backend cart changed.
   */
  async function migrateLegacyCart(snapshot: api.ApiStateSnapshot, legacyLines: CartLine[]): Promise<boolean> {
    if (!snapshot.cart) return false; // A backend without the cart API: try again next boot.
    let posted = false;
    if (legacyLines.length > 0 && snapshot.cart.lines.length === 0) {
      const lines = legacyLines.map((l) => ({ id: l.id, objectId: l.objectId, qty: Math.max(1, clampQty(l.qty)) }));
      try {
        await api.addCartLines(lines);
        posted = true;
      } catch (error) {
        // The backend refused the old lines: they can't be recovered, so drop them. Other failures retry on the next boot.
        if (!(error instanceof ApiError && error.status >= 400 && error.status < 500)) throw error;
      }
    }
    stripLegacyCart();
    return posted;
  }

  /** Reloads the cache from the backend once every pending write has landed. */
  function resync(): void {
    if (status.phase !== "ready" || resyncQueued) return;
    resyncQueued = true;
    queue
      .run(async () => {
        resyncQueued = false;
        const snapshot = await api.getState();
        // Writes queued meanwhile are already applied locally; a snapshot from before them would undo them.
        if (queue.hasOthersPending()) resync();
        else hydrate(snapshot);
      })
      .catch((error: unknown) => {
        resyncQueued = false;
        reportError(error, false);
      });
  }

  function reportError(error: unknown, thenResync: boolean): void {
    if (error instanceof ApiError && error.isUnreachable) {
      toast.error("Can't reach the backend", {
        id: SAVE_TOAST_ID,
        description: "Recent changes may not be saved. Start it with ./start.sh.",
      });
    } else {
      toast.error("Couldn't save your change", {
        id: SAVE_TOAST_ID,
        description: `${describeError(error)}. Showing what the backend has.`,
      });
    }
    if (thenResync) resync();
  }

  const onWriteError = (error: unknown) => reportError(error, true);

  function write(task: () => Promise<unknown>): void {
    queue.run(task).catch(onWriteError);
  }

  function writeCoalesced(key: string, task: () => Promise<unknown>): void {
    queue.debounce(key, task, COALESCE_MS, onWriteError);
  }

  async function bootOnce(): Promise<void> {
    setStatus({ phase: "loading" });
    // Read before anything writes localStorage, which would drop the old cart.
    const legacyCart = hasLegacyCart() ? readLegacyCart() : null;
    try {
      let snapshot = await api.getState();
      if (snapshot.spaces.length === 0 && snapshot.objects.length === 0) {
        const legacy = readLegacy();
        const hasLegacyData = legacy !== null && (legacy.spaces.length > 0 || legacy.objects.length > 0);
        await api.importState(hasLegacyData ? legacyImportPayload(legacy) : createSeedState());
        if (legacy) {
          dispatch({ type: "local/replace", local: legacyLocal(state, legacy) });
          removeLegacy();
        }
        snapshot = await api.getState();
      }
      if (legacyCart && (await migrateLegacyCart(snapshot, legacyCart))) snapshot = await api.getState();
      hydrate(snapshot);
      setStatus({ phase: "ready" });
    } catch (error) {
      setStatus({ phase: "error", error });
    }
  }

  function boot(): Promise<void> {
    booting ??= bootOnce().finally(() => {
      booting = null;
    });
    return booting;
  }

  function refreshIfStale(): void {
    if (Date.now() - lastSyncAt > REFRESH_ON_FOCUS_MS) resync();
  }

  const actions: AppActions = {
    createSpace(input) {
      const id = makeId("space");
      dispatch({ type: "space/create", id, input, now: nowIso() });
      const space = getSpace(state, id);
      if (space) write(() => api.createSpace(spaceCreateBody(space)));
      return id;
    },
    updateSpace(id, patch) {
      const prev = getSpace(state, id);
      if (!prev) return;
      dispatch({ type: "space/update", id, patch, now: nowIso() });
      const next = getSpace(state, id);
      const body = next ? spacePatchBody(prev, next) : null;
      if (body) write(() => api.updateSpace(id, body));
    },
    setSpaceVibeBoard: (id, vibeBoardId) => dispatch({ type: "space/set-vibe-board", id, vibeBoardId }),
    deleteSpace(id) {
      if (!getSpace(state, id)) return;
      dispatch({ type: "space/delete", id });
      write(() => ignoreMissing(() => api.deleteSpace(id)));
    },

    placeObject(spaceId, objectId, pos) {
      const space = getSpace(state, spaceId);
      const obj = getObject(state, objectId);
      if (!space || !obj || space.placements.some((p) => p.objectId === objectId)) return;
      // Decided here so the cache and the backend get the same spot.
      const at = pos ?? findOpenPosition(state, spaceId, footprint(obj, space));
      dispatch({ type: "placement/place", spaceId, objectId, pos: at, now: nowIso() });
      write(() => api.putPlacement(spaceId, objectId, at));
    },
    moveObject(spaceId, objectId, pos) {
      if (!getSpace(state, spaceId)?.placements.some((p) => p.objectId === objectId)) return;
      dispatch({ type: "placement/move", spaceId, objectId, pos, now: nowIso() });
      writeCoalesced(`placement:${spaceId}:${objectId}`, () => api.putPlacement(spaceId, objectId, pos));
    },
    removeFromSpace(spaceId, objectId) {
      if (!getSpace(state, spaceId)?.placements.some((p) => p.objectId === objectId)) return;
      dispatch({ type: "placement/remove", spaceId, objectId, now: nowIso() });
      write(() => ignoreMissing(() => api.deletePlacement(spaceId, objectId)));
    },
    restorePlacement(spaceId, placement) {
      if (!getSpace(state, spaceId) || !getObject(state, placement.objectId)) return;
      dispatch({ type: "placement/restore", spaceId, placement, now: nowIso() });
      write(() => api.putPlacement(spaceId, placement.objectId, placement));
    },

    createObject(input) {
      const id = input.id ?? makeId("obj");
      const object: SpaceObject = {
        dims: { x: 24, y: 24 },
        similarItems: [],
        referencePins: [],
        source: "search",
        createdAt: nowIso(),
        ...input,
        id,
        candidateIds: [],
        selectedProductId: undefined,
        search: { status: "idle" },
        votes: {},
      };
      dispatch({ type: "object/create", object });
      write(() => api.createObject(objectCreateBody(object)));
      return id;
    },
    updateObject(id, patch) {
      const prev = getObject(state, id);
      if (!prev) return;
      dispatch({ type: "object/update", id, patch });
      const next = getObject(state, id);
      const body = next ? objectPatchBody(prev, next) : null;
      if (body) write(() => api.updateObject(id, body));
    },
    resizeObject(id, size, spaceId) {
      const space = getSpace(state, spaceId);
      const obj = getObject(state, id);
      if (!space || !obj) return;
      const dims = resizedDims(obj, space, size);
      dispatch({ type: "object/update", id, patch: { dims } });
      writeCoalesced(`dims:${id}`, () => api.updateObject(id, { dims }));
    },
    deleteObject(id) {
      if (!getObject(state, id)) return;
      const line = getCartLineForObject(state, id);
      const store = line ? storeOfLine(state, line) : undefined;
      dispatch({ type: "object/delete", id });
      // The backend also cancels the object's active search runs and deletes its cart line.
      if (store) {
        writeCart([store], async () => {
          await ignoreMissing(() => api.deleteObject(id));
          return [store];
        });
      } else {
        write(() => ignoreMissing(() => api.deleteObject(id)));
      }
    },

    setSearchState: (id, search) => dispatch({ type: "search/set-state", id, search }),
    setPreferences: (objectId, preferences) => dispatch({ type: "preferences/set", objectId, preferences }),
    applySearchResults(objectId, candidates, finishedAt, opts) {
      const obj = getObject(state, objectId);
      if (!obj) return;
      const accepted = candidates.filter((c) => c.verdict === "accepted" && c.objectId === objectId);
      // Results never auto-select: the user picks a card. Clear any previous pick on the backend too.
      const line = getCartLineForObject(state, objectId);
      const oldStore = line ? storeOfLine(state, line) : undefined;
      const keepSelection = opts?.keepSelection ?? false;
      dispatch({
        type: "search/results",
        id: objectId,
        candidates: accepted,
        lastRunAt: finishedAt ?? nowIso(),
        keepSelection,
      });
      if (!obj.selectedProductId || keepSelection) return;
      if (oldStore) {
        // Clearing the pick deletes the object's cart line on the backend.
        writeCart([oldStore], async () => {
          await api.selectCandidate(objectId, null);
          return [oldStore];
        });
      } else {
        write(() => api.selectCandidate(objectId, null));
      }
    },
    selectProduct(objectId, productId) {
      const obj = getObject(state, objectId);
      if (!obj || obj.selectedProductId === productId) return;
      const line = getCartLineForObject(state, objectId);
      const oldStore = line ? storeOfLine(state, line) : undefined;
      if (productId === undefined) {
        dispatch({ type: "product/select", objectId, productId: undefined });
        if (oldStore) {
          // Clearing the pick deletes the object's cart line on the backend.
          writeCart([oldStore], async () => {
            await api.selectCandidate(objectId, null);
            return [oldStore];
          });
        } else {
          write(() => api.selectCandidate(objectId, null));
        }
        return;
      }
      dispatch({ type: "product/select", objectId, productId });
      const candidateId = candidateIdOf(productId);
      if (candidateId === undefined) return;
      if (line) {
        // The cart line follows the pick, possibly into another store: sync both.
        const stores = [oldStore, getProduct(state, productId)?.storeId];
        writeCart(stores, async () => {
          await api.selectCandidate(objectId, candidateId);
          return stores.filter((id): id is string => Boolean(id));
        });
      } else {
        write(() => api.selectCandidate(objectId, candidateId));
      }
    },

    voteProduct(objectId, productId, vote) {
      const obj = getObject(state, objectId);
      const gid = getProduct(state, productId)?.productGid;
      const candidateId = candidateIdOf(productId);
      if (!obj || !gid || candidateId === undefined) return;
      const before = obj.votes ?? {};
      if ((before[gid] ?? null) === vote) return;
      const optimistic = { ...before };
      if (vote === null) delete optimistic[gid];
      else optimistic[gid] = vote;
      dispatch({ type: "votes/set", objectId, votes: optimistic });
      write(async () => {
        try {
          const result = await api.voteCandidate(objectId, candidateId, vote);
          // Adopt the backend's map unless a newer vote is still waiting to be sent.
          if (!queue.hasOthersPending()) dispatch({ type: "votes/set", objectId, votes: result.votes });
        } catch (error) {
          const current = getObject(state, objectId);
          if (current) {
            const rolled = { ...(current.votes ?? {}) };
            if (before[gid]) rolled[gid] = before[gid];
            else delete rolled[gid];
            dispatch({ type: "votes/set", objectId, votes: rolled });
          }
          throw error;
        }
      });
    },

    addToCart(objectId) {
      const obj = getObject(state, objectId);
      if (!obj || obj.source !== "search" || !obj.selectedProductId) return false;
      const id = getCartLineForObject(state, objectId)?.id ?? makeId("line");
      dispatch({ type: "cart/add", objectIds: [objectId], ids: [id] });
      const line = getCartLineForObject(state, objectId);
      if (line) writeCart(storesOfLines(state, [line]), () => postLines([{ id: line.id, objectId }]));
      return true;
    },
    addAllToCart(objectIds) {
      const current = state;
      const eligible = objectIds.filter((id) => {
        const obj = getObject(current, id);
        return obj && obj.source === "search" && obj.selectedProductId && !current.cart.some((l) => l.objectId === id);
      });
      dispatch({ type: "cart/add", objectIds, ids: objectIds.map(() => makeId("line")) });
      const wanted = new Set(objectIds);
      const lines = state.cart.filter((l) => wanted.has(l.objectId));
      if (lines.length > 0) {
        writeCart(storesOfLines(state, lines), () => postLines(lines.map((l) => ({ id: l.id, objectId: l.objectId }))));
      }
      return eligible.length;
    },
    setQty(lineId, qty) {
      const line = state.cart.find((l) => l.id === lineId);
      if (!line || !Number.isFinite(qty)) return;
      const next = clampQty(qty);
      if (next === line.qty) return;
      const store = storeOfLine(state, line);
      dispatch({ type: "cart/set-qty", lineId, qty: next });
      writeCart([store], () => dirtyOrNone(() => api.updateCartLine(lineId, next)), `cart-qty:${lineId}`);
    },
    removeLine(lineId) {
      const line = state.cart.find((l) => l.id === lineId);
      if (!line) return;
      const store = storeOfLine(state, line);
      dispatch({ type: "cart/remove", lineId });
      writeCart([store], () => dirtyOrNone(() => api.deleteCartLine(lineId)));
    },
    clearCart() {
      const stores = storesOfLines(state, state.cart);
      dispatch({ type: "cart/clear" });
      writeCart(stores, async () => (await api.clearCart()).dirtyStores ?? stores);
    },
    syncStore(storeId) {
      scheduleSync([storeId], { force: true, delayMs: 0 });
    },
    async checkout(storeId) {
      try {
        // Waits for every pending write, so the checkout holds the cart as shown, but runs outside the
        // queue: it calls the merchant (the backend syncs first) and shouldn't hold up later edits.
        await queue.run(async () => {});
        const result = await api.checkoutStore(storeId);
        if (isMerchantCart(result.store)) dispatch({ type: "merchant/synced", storeId, cart: result.store });
        if (!isHttpsUrl(result.url)) throw new Error("The store sent a checkout link that isn't secure.");
        return { url: result.url, kind: result.kind };
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          const body = error.payload as Partial<api.ApiCheckoutUnavailable> | undefined;
          if (isMerchantCart(body?.store)) dispatch({ type: "merchant/synced", storeId, cart: body.store });
          throw new Error(checkoutUnavailableMessage(body?.details?.reason ?? error.details?.reason));
        }
        throw error;
      }
    },

    importFromShopify() {
      if (state.importedAt) return;
      const now = nowIso();
      const existing = new Set(state.objects.map((o) => o.id));
      const objects = createImportedObjects(now).filter((o) => !existing.has(o.id));
      dispatch({ type: "import", objects, now });
      for (const object of objects) write(() => api.createObject(objectCreateBody(object)));
    },
    resetDemo() {
      const seed = createSeedState();
      // Deleting the objects deletes their cart lines; those merchant carts then need emptying.
      const cartStores = storesOfLines(state, state.cart);
      dispatch({ type: "reset", seed });
      pendingResets++;
      resetListeners.forEach((l) => l());
      write(async () => {
        try {
          // Everything the backend has, including what other tabs added.
          const current = await api.getState();
          for (const space of current.spaces) await ignoreMissing(() => api.deleteSpace(space.id));
          for (const object of current.objects) await ignoreMissing(() => api.deleteObject(object.id));
          await api.resetState();
          await api.importState(seed);
          const snapshot = await api.getState();
          if (queue.hasOthersPending()) resync();
          else hydrate(snapshot);
          scheduleSync(cartStores);
        } finally {
          pendingResets--;
          if (pendingResets === 0) resetSettledListeners.forEach((l) => l());
        }
      });
    },
  };

  return {
    getState: () => state,
    getStatus: () => status,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    actions,
    enqueue: (task) => queue.run(task),
    onReset(listener) {
      resetListeners.add(listener);
      return () => {
        resetListeners.delete(listener);
      };
    },
    isResetting: () => pendingResets > 0,
    onResetSettled(listener) {
      resetSettledListeners.add(listener);
      return () => {
        resetSettledListeners.delete(listener);
      };
    },
    retry: () => void boot(),
    start() {
      void boot();
      // Spaces, objects and the cart come from the backend; only the local part syncs between tabs directly.
      const onStorage = (e: StorageEvent) => {
        if (e.key !== LOCAL_KEY) return;
        dispatch({ type: "local/replace", local: parseLocal(e.newValue) ?? EMPTY_LOCAL }, { persist: false });
        // The other tab probably changed backend data as well.
        refreshIfStale();
      };
      const onVisible = () => {
        if (document.visibilityState === "visible") refreshIfStale();
      };
      window.addEventListener("storage", onStorage);
      window.addEventListener("focus", refreshIfStale);
      document.addEventListener("visibilitychange", onVisible);
      return () => {
        window.removeEventListener("storage", onStorage);
        window.removeEventListener("focus", refreshIfStale);
        document.removeEventListener("visibilitychange", onVisible);
      };
    },
  };
}
