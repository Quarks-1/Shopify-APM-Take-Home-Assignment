import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { AppState } from "@/mocks/types";
import { getCartCount } from "@/state/selectors";
import { StoreContext } from "@/state/store-context";
import { createAppStore, type AppActions } from "@/state/store";

export type { AppActions, ObjectPatch, SpaceInput, SpacePatch } from "@/state/store";

const StateContext = createContext<AppState | null>(null);
const ActionsContext = createContext<AppActions | null>(null);

/** Loads spaces, objects and the cart from the backend and keeps them in sync. */
export function AppStoreProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createAppStore);
  const state = useSyncExternalStore(store.subscribe, store.getState);

  useEffect(() => store.start(), [store]);

  return (
    <StoreContext.Provider value={store}>
      <ActionsContext.Provider value={store.actions}>
        <StateContext.Provider value={state}>{children}</StateContext.Provider>
      </ActionsContext.Provider>
    </StoreContext.Provider>
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
  return getCartCount(useAppState());
}
