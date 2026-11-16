import { createContext, useContext, useSyncExternalStore } from "react";
import type { AppStatus, AppStore, StoreInternals } from "@/state/store";

/** The whole store; provided by AppStoreProvider. */
export const StoreContext = createContext<AppStore | null>(null);

function useStore(): AppStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error("Store hooks must be used inside AppStoreProvider");
  return store;
}

/** Whether the first load from the backend is still running, done, or failed (with a retry). */
export function useAppStatus(): { status: AppStatus; retry: () => void } {
  const store = useStore();
  const status = useSyncExternalStore(store.subscribe, store.getStatus);
  return { status, retry: store.retry };
}

/** Latest state and the write queue, for code that works outside of rendering (search polling). */
export function useStoreInternals(): StoreInternals {
  return useStore();
}
