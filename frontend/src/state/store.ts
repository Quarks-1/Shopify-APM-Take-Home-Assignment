// The app's state container. The backend owns spaces, objects, placements,
// search state, candidates and selection; this store is an in-memory cache of
// them plus the local part (cart, checkout marks, import time) kept in
// localStorage. Actions update the cache synchronously, then send the change
// to the backend through a serial write queue (optimistic updates). A failed
// write shows a toast and reloads the cache from GET /api/state/.
import { toast } from "@/components/base/toast/toast";
import * as api from "@/lib/api";
import { ApiError, describeError } from "@/lib/api";
import { createImportedObjects, createSeedState, type SeedState } from "@/mocks/seed";
import type {
  AppState,
  CartLine,
  LocalState,
  ObjectDims,
  ObjectType,
  Placement,
  SearchState,
  Space,
  SpaceObject,
} from "@/mocks/types";
import {
  EMPTY_LOCAL,
  LOCAL_KEY,
  candidateIdOf,
  candidateProductId,
  legacyImportPayload,
  localOf,
  mergeCandidates,
  objectCreateBody,
  objectPatchBody,
  parseLocal,
  readLegacy,
  readLocal,
  removeLegacy,
  snapshotToState,
  spaceCreateBody,
  spacePatchBody,
  withSelectionFirst,
  writeLocal,
} from "@/state/remote";
import { findOpenPosition, footprint, getObject, getSpace } from "@/state/selectors";
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

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type Action =
  | { type: "hydrate"; snapshot: api.ApiStateSnapshot }
  | { type: "reset"; seed: SeedState }
  | { type: "local/replace"; local: LocalState }
  | { type: "space/create"; id: string; input: SpaceInput; now: string }
  | { type: "space/update"; id: string; patch: SpacePatch; now: string }
  | { type: "space/delete"; id: string }
  | { type: "placement/place"; spaceId: string; objectId: string; pos: Pos; now: string }
  | { type: "placement/move"; spaceId: string; objectId: string; pos: Pos; now: string }
  | { type: "placement/remove"; spaceId: string; objectId: string; now: string }
  | { type: "placement/restore"; spaceId: string; placement: Placement; now: string }
  | { type: "object/create"; object: SpaceObject }
  | { type: "object/update"; id: string; patch: ObjectPatch }
  | { type: "object/delete"; id: string }
  | { type: "search/set-state"; id: string; search: SearchState }
  | { type: "search/results"; id: string; candidates: api.ApiCandidate[]; selected?: string; lastRunAt: string }
  | { type: "product/select"; objectId: string; productId: string }
  | { type: "cart/add"; objectIds: string[]; ids: string[] }
  | { type: "cart/set-qty"; lineId: string; qty: number }
  | { type: "cart/remove"; lineId: string }
  | { type: "cart/clear" }
  | { type: "checkout/opened"; storeId: string; now: string }
  | { type: "import"; objects: SpaceObject[]; now: string };

function mapSpace(state: AppState, id: string, fn: (space: Space) => Space): AppState {
  return { ...state, spaces: state.spaces.map((s) => (s.id === id ? fn(s) : s)) };
}

function mapObject(state: AppState, id: string, fn: (o: SpaceObject) => SpaceObject): AppState {
  return { ...state, objects: state.objects.map((o) => (o.id === id ? fn(o) : o)) };
}

function setCartProduct(cart: CartLine[], objectId: string, productId: string): CartLine[] {
  return cart.map((l) => (l.objectId === objectId ? { ...l, productId } : l));
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
      const ids = action.candidates.map((c) => candidateProductId(c.id));
      const { selected } = action;
      const next: AppState = {
        ...mapObject(state, action.id, (o) => ({
          ...o,
          candidateIds: withSelectionFirst(ids, selected),
          selectedProductId: selected,
          search: { status: ids.length > 0 ? "done" : "empty", lastRunAt: action.lastRunAt },
        })),
        products,
        stores,
      };
      return selected && selected !== obj.selectedProductId
        ? { ...next, cart: setCartProduct(next.cart, action.id, selected) }
        : next;
    }

    case "product/select": {
      const next = mapObject(state, action.objectId, (o) => ({
        ...o,
        selectedProductId: action.productId,
        candidateIds: withSelectionFirst(o.candidateIds, action.productId),
      }));
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
    case "checkout/opened":
      return { ...state, checkoutOpened: { ...state.checkoutOpened, [action.storeId]: action.now } };

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
  applySearchResults(objectId: string, candidates: api.ApiCandidate[], finishedAt?: string | null): void;
  selectProduct(objectId: string, productId: string): void;

  addToCart(objectId: string): boolean;
  addAllToCart(objectIds: string[]): number;
  setQty(lineId: string, qty: number): void;
  removeLine(lineId: string): void;
  clearCart(): void;
  markCheckoutOpened(storeId: string): void;

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

/** Object dims after resizing its footprint on a space's plane (wall: x and z, otherwise x and y). */
function resizedDims(dims: ObjectDims, space: Space, { w, h }: { w: number; h: number }): ObjectDims {
  return space.type === "wall" ? { ...dims, x: w, z: h } : { ...dims, x: w, y: h };
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

function legacyLocal(current: LocalState, legacy: LocalState): LocalState {
  // The old cart points at mock products, so it stays behind; the rest carries over.
  const old =
    parseLocal(JSON.stringify({ cart: [], checkoutOpened: legacy.checkoutOpened, importedAt: legacy.importedAt })) ??
    EMPTY_LOCAL;
  return {
    cart: current.cart,
    checkoutOpened: { ...old.checkoutOpened, ...current.checkoutOpened },
    importedAt: current.importedAt ?? old.importedAt,
  };
}

export function createAppStore(): AppStore {
  let state: AppState = { version: 1, spaces: [], objects: [], products: {}, stores: {}, ...(readLocal() ?? EMPTY_LOCAL) };
  let status: AppStatus = { phase: "loading" };
  let lastSyncAt = 0;
  let resyncQueued = false;
  let booting: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  const resetListeners = new Set<() => void>();
  const queue = new WriteQueue();

  const emit = () => listeners.forEach((l) => l());

  function dispatch(action: Action, { persist = true }: { persist?: boolean } = {}): void {
    const prev = state;
    const next = reducer(prev, action);
    if (next === prev) return;
    state = next;
    const localChanged =
      next.cart !== prev.cart || next.checkoutOpened !== prev.checkoutOpened || next.importedAt !== prev.importedAt;
    if (persist && localChanged) writeLocal(next);
    emit();
  }

  function setStatus(next: AppStatus): void {
    status = next;
    emit();
  }

  function hydrate(snapshot: api.ApiStateSnapshot): void {
    lastSyncAt = Date.now();
    dispatch({ type: "hydrate", snapshot });
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
        source: "search",
        createdAt: nowIso(),
        ...input,
        id,
        candidateIds: [],
        selectedProductId: undefined,
        search: { status: "idle" },
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
      const dims = resizedDims(obj.dims, space, size);
      dispatch({ type: "object/update", id, patch: { dims } });
      writeCoalesced(`dims:${id}`, () => api.updateObject(id, { dims }));
    },
    deleteObject(id) {
      if (!getObject(state, id)) return;
      dispatch({ type: "object/delete", id });
      // The backend also cancels the object's active search runs.
      write(() => ignoreMissing(() => api.deleteObject(id)));
    },

    setSearchState: (id, search) => dispatch({ type: "search/set-state", id, search }),
    applySearchResults(objectId, candidates, finishedAt) {
      const obj = getObject(state, objectId);
      if (!obj) return;
      const accepted = candidates.filter((c) => c.verdict === "accepted" && c.objectId === objectId);
      const ids = accepted.map((c) => candidateProductId(c.id));
      // Keep the pick when the new run still has it, otherwise take the best match (none when empty).
      const selected = obj.selectedProductId && ids.includes(obj.selectedProductId) ? obj.selectedProductId : ids[0];
      dispatch({ type: "search/results", id: objectId, candidates: accepted, selected, lastRunAt: finishedAt ?? nowIso() });
      if (selected !== obj.selectedProductId) {
        const candidateId = candidateIdOf(selected) ?? null;
        write(() => api.selectCandidate(objectId, candidateId));
      }
    },
    selectProduct(objectId, productId) {
      const obj = getObject(state, objectId);
      if (!obj || obj.selectedProductId === productId) return;
      dispatch({ type: "product/select", objectId, productId });
      const candidateId = candidateIdOf(productId);
      if (candidateId !== undefined) write(() => api.selectCandidate(objectId, candidateId));
    },

    addToCart(objectId) {
      const obj = getObject(state, objectId);
      if (!obj || obj.source !== "search" || !obj.selectedProductId) return false;
      dispatch({ type: "cart/add", objectIds: [objectId], ids: [makeId("line")] });
      return true;
    },
    addAllToCart(objectIds) {
      const current = state;
      const eligible = objectIds.filter((id) => {
        const obj = getObject(current, id);
        return obj && obj.source === "search" && obj.selectedProductId && !current.cart.some((l) => l.objectId === id);
      });
      dispatch({ type: "cart/add", objectIds, ids: objectIds.map(() => makeId("line")) });
      return eligible.length;
    },
    setQty: (lineId, qty) => dispatch({ type: "cart/set-qty", lineId, qty }),
    removeLine: (lineId) => dispatch({ type: "cart/remove", lineId }),
    clearCart: () => dispatch({ type: "cart/clear" }),
    markCheckoutOpened: (storeId) => dispatch({ type: "checkout/opened", storeId, now: nowIso() }),

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
      dispatch({ type: "reset", seed });
      resetListeners.forEach((l) => l());
      write(async () => {
        // Everything the backend has, including what other tabs added.
        const current = await api.getState();
        for (const space of current.spaces) await ignoreMissing(() => api.deleteSpace(space.id));
        for (const object of current.objects) await ignoreMissing(() => api.deleteObject(object.id));
        await api.importState(seed);
        const snapshot = await api.getState();
        if (queue.hasOthersPending()) resync();
        else hydrate(snapshot);
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
    retry: () => void boot(),
    start() {
      void boot();
      // Spaces and objects come from the backend; only the local part syncs between tabs directly.
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
