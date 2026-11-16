import { createContext, useLayoutEffect, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { createImportedObjects, createSeedState } from "@/mocks/seed";
import type {
  AppState,
  CartLine,
  ObjectType,
  Placement,
  SearchState,
  Space,
  SpaceObject,
} from "@/mocks/types";
import { findOpenPosition, footprint, getObject, getSpace } from "@/state/selectors";

export const STORAGE_KEY = "wireframe-state-v1";

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

function normalizeLoaded(state: AppState): AppState {
  return {
    ...state,
    objects: state.objects.map((o) => {
      if (o.search.status !== "running") return o;
      return { ...o, search: { status: o.candidateIds.length > 0 ? "done" : "idle", lastRunAt: o.search.lastRunAt } };
    }),
  };
}

function parseState(raw: string | null): AppState | null {
  if (!raw) return null;
  try {
    const data: unknown = JSON.parse(raw);
    if (typeof data !== "object" || data === null) return null;
    const candidate = data as Partial<AppState>;
    if (
      candidate.version !== 1 ||
      !Array.isArray(candidate.spaces) ||
      !Array.isArray(candidate.objects) ||
      !Array.isArray(candidate.cart) ||
      typeof candidate.checkoutOpened !== "object" ||
      candidate.checkoutOpened === null
    ) {
      return null;
    }
    return normalizeLoaded(candidate as AppState);
  } catch {
    return null;
  }
}

function loadState(): AppState {
  try {
    return parseState(localStorage.getItem(STORAGE_KEY)) ?? createSeedState();
  } catch {
    return createSeedState();
  }
}

function saveState(state: AppState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage may be unavailable or full; the prototype keeps working in memory.
  }
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type SpaceInput = Omit<Space, "id" | "placements" | "createdAt" | "updatedAt">;
type SpacePatch = Partial<Omit<Space, "id" | "placements">>;
type ObjectPatch = Partial<Omit<SpaceObject, "id">>;
type Pos = { x: number; y: number };

type Action =
  | { type: "hydrate"; state: AppState }
  | { type: "reset"; state: AppState }
  | { type: "space/create"; id: string; input: SpaceInput; now: string }
  | { type: "space/update"; id: string; patch: SpacePatch; now: string }
  | { type: "space/delete"; id: string }
  | { type: "placement/place"; spaceId: string; objectId: string; pos?: Pos; now: string }
  | { type: "placement/move"; spaceId: string; objectId: string; pos: Pos; now: string }
  | { type: "placement/remove"; spaceId: string; objectId: string; now: string }
  | { type: "placement/restore"; spaceId: string; placement: Placement; now: string }
  | { type: "object/create"; id: string; object: SpaceObject }
  | { type: "object/update"; id: string; patch: ObjectPatch }
  | { type: "object/resize"; id: string; size: { w: number; h: number }; spaceId: string }
  | { type: "object/delete"; id: string }
  | { type: "search/set-state"; id: string; search: SearchState }
  | { type: "search/set-candidates"; id: string; candidateIds: string[]; now: string }
  | { type: "product/select"; objectId: string; productId: string }
  | { type: "cart/add"; objectIds: string[]; ids: string[] }
  | { type: "cart/set-qty"; lineId: string; qty: number }
  | { type: "cart/remove"; lineId: string }
  | { type: "cart/clear" }
  | { type: "checkout/opened"; storeId: string; now: string }
  | { type: "import"; now: string };

function mapSpace(state: AppState, id: string, fn: (space: Space) => Space): AppState {
  return { ...state, spaces: state.spaces.map((s) => (s.id === id ? fn(s) : s)) };
}

function mapObject(state: AppState, id: string, fn: (o: SpaceObject) => SpaceObject): AppState {
  return { ...state, objects: state.objects.map((o) => (o.id === id ? fn(o) : o)) };
}

function withSelectionFirst(candidateIds: string[], selected?: string): string[] {
  if (!selected || !candidateIds.includes(selected)) return candidateIds;
  return [selected, ...candidateIds.filter((id) => id !== selected)];
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
    case "reset":
      return action.state;

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

    case "placement/place": {
      const space = getSpace(state, action.spaceId);
      const obj = getObject(state, action.objectId);
      if (!space || !obj || space.placements.some((p) => p.objectId === action.objectId)) return state;
      const pos = action.pos ?? findOpenPosition(state, action.spaceId, footprint(obj, space));
      return mapSpace(state, action.spaceId, (s) => ({
        ...s,
        placements: [...s.placements, { objectId: action.objectId, ...pos }],
        updatedAt: action.now,
      }));
    }
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
    case "object/resize": {
      const space = getSpace(state, action.spaceId);
      if (!space) return state;
      const { w, h } = action.size;
      return mapObject(state, action.id, (o) => ({
        ...o,
        dims: space.type === "wall" ? { ...o.dims, x: w, z: h } : { ...o.dims, x: w, y: h },
      }));
    }
    case "object/delete":
      return {
        ...state,
        objects: state.objects.filter((o) => o.id !== action.id),
        spaces: state.spaces.map((s) => ({ ...s, placements: s.placements.filter((p) => p.objectId !== action.id) })),
        cart: state.cart.filter((l) => l.objectId !== action.id),
      };

    case "search/set-state":
      return mapObject(state, action.id, (o) => ({ ...o, search: action.search }));
    case "search/set-candidates": {
      const obj = getObject(state, action.id);
      if (!obj) return state;
      const { candidateIds } = action;
      const selected =
        obj.selectedProductId && candidateIds.includes(obj.selectedProductId)
          ? obj.selectedProductId
          : candidateIds[0];
      const next = mapObject(state, action.id, (o) => ({
        ...o,
        candidateIds: withSelectionFirst(candidateIds, selected),
        selectedProductId: selected,
        search: { status: candidateIds.length > 0 ? "done" : "empty", lastRunAt: action.now },
      }));
      return selected && selected !== obj.selectedProductId
        ? { ...next, cart: setCartProduct(next.cart, action.id, selected) }
        : next;
    }

    case "product/select": {
      const obj = getObject(state, action.objectId);
      if (!obj) return state;
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
      return {
        ...state,
        objects: [...state.objects, ...createImportedObjects(action.now)],
        importedAt: action.now,
      };
  }
}

// ---------------------------------------------------------------------------
// Public API
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

  setSearchState(id: string, search: SearchState): void;
  setCandidates(id: string, candidateIds: string[]): void;
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

const StateContext = createContext<AppState | null>(null);
const ActionsContext = createContext<AppActions | null>(null);

function makeId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${random}`;
}

const nowIso = () => new Date().toISOString();

export function AppStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, loadState);

  // Latest state for actions that must answer synchronously (addToCart etc.).
  const stateRef = useRef(state);
  useLayoutEffect(() => {
    stateRef.current = state;
  });

  useEffect(() => {
    saveState(state);
  }, [state]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      const next = parseState(e.newValue);
      if (next) dispatch({ type: "hydrate", state: next });
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const actions = useMemo<AppActions>(
    () => ({
      createSpace(input) {
        const id = makeId("space");
        dispatch({ type: "space/create", id, input, now: nowIso() });
        return id;
      },
      updateSpace: (id, patch) => dispatch({ type: "space/update", id, patch, now: nowIso() }),
      deleteSpace: (id) => dispatch({ type: "space/delete", id }),

      placeObject: (spaceId, objectId, pos) =>
        dispatch({ type: "placement/place", spaceId, objectId, pos, now: nowIso() }),
      moveObject: (spaceId, objectId, pos) =>
        dispatch({ type: "placement/move", spaceId, objectId, pos, now: nowIso() }),
      removeFromSpace: (spaceId, objectId) =>
        dispatch({ type: "placement/remove", spaceId, objectId, now: nowIso() }),
      restorePlacement: (spaceId, placement) =>
        dispatch({ type: "placement/restore", spaceId, placement, now: nowIso() }),

      createObject(input) {
        const id = input.id ?? makeId("obj");
        const object: SpaceObject = {
          dims: { x: 24, y: 24 },
          similarItems: [],
          source: "search",
          candidateIds: [],
          search: { status: "idle" },
          createdAt: nowIso(),
          ...input,
          id,
        };
        dispatch({ type: "object/create", id, object });
        return id;
      },
      updateObject: (id, patch) => dispatch({ type: "object/update", id, patch }),
      resizeObject: (id, size, spaceId) => dispatch({ type: "object/resize", id, size, spaceId }),
      deleteObject: (id) => dispatch({ type: "object/delete", id }),

      setSearchState: (id, search) => dispatch({ type: "search/set-state", id, search }),
      setCandidates: (id, candidateIds) =>
        dispatch({ type: "search/set-candidates", id, candidateIds, now: nowIso() }),
      selectProduct: (objectId, productId) => dispatch({ type: "product/select", objectId, productId }),

      addToCart(objectId) {
        const obj = getObject(stateRef.current, objectId);
        if (!obj || obj.source !== "search" || !obj.selectedProductId) return false;
        dispatch({ type: "cart/add", objectIds: [objectId], ids: [makeId("line")] });
        return true;
      },
      addAllToCart(objectIds) {
        const current = stateRef.current;
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

      importFromShopify: () => dispatch({ type: "import", now: nowIso() }),
      resetDemo() {
        try {
          localStorage.removeItem(STORAGE_KEY);
        } catch {
          // Ignore unavailable storage.
        }
        dispatch({ type: "reset", state: createSeedState() });
      },
    }),
    [],
  );

  return (
    <ActionsContext.Provider value={actions}>
      <StateContext.Provider value={state}>{children}</StateContext.Provider>
    </ActionsContext.Provider>
  );
}

export function useAppState(): AppState {
  const state = useContext(StateContext);
  if (!state) throw new Error("useAppState must be used inside AppStoreProvider");
  return state;
}

export function useAppActions(): AppActions {
  const actions = useContext(ActionsContext);
  if (!actions) throw new Error("useAppActions must be used inside AppStoreProvider");
  return actions;
}

export function useCartCount(): number {
  return useAppState().cart.reduce((sum, l) => sum + l.qty, 0);
}
