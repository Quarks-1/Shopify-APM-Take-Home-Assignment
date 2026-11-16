import { createContext, useCallback, useLayoutEffect, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { mockSearch } from "@/mocks/catalog";
import { useAppActions, useAppState } from "@/state/app-store";

export interface ObjectSearchApi {
  search(objectId: string): void;
  searchAll(objectIds: string[]): void;
  cancel(objectId: string): void;
  isRunning(objectId: string): boolean;
}

const ObjectSearchContext = createContext<ObjectSearchApi | null>(null);

const STAGGER_MS = 300;

/** Runs searches outside of page components so they survive navigation. Render inside AppStoreProvider. */
export function ObjectSearchProvider({ children }: { children: ReactNode }) {
  const state = useAppState();
  const actions = useAppActions();

  const stateRef = useRef(state);
  useLayoutEffect(() => {
    stateRef.current = state;
  });

  const controllers = useRef(new Map<string, AbortController>());
  const staggerTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const pending = useRef(new Set<string>());
  // Bumped whenever a run starts or ends so isRunning consumers re-render.
  const [, setVersion] = useState(0);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const run = useCallback(
    (objectId: string) => {
      const object = stateRef.current.objects.find((o) => o.id === objectId);
      if (!object || object.source === "imported") return;

      controllers.current.get(objectId)?.abort();
      const controller = new AbortController();
      controllers.current.set(objectId, controller);

      actions.setSearchState(objectId, { status: "running", step: "planning", lastRunAt: object.search.lastRunAt });
      bump();

      mockSearch(
        object,
        (step) => {
          if (controller.signal.aborted) return;
          actions.setSearchState(objectId, { status: "running", step, lastRunAt: object.search.lastRunAt });
        },
        controller.signal,
      )
        .then((ids) => {
          if (controller.signal.aborted) return;
          actions.setCandidates(objectId, ids);
        })
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          actions.setSearchState(objectId, { status: "idle", lastRunAt: object.search.lastRunAt });
        })
        .finally(() => {
          if (controllers.current.get(objectId) === controller) {
            controllers.current.delete(objectId);
            bump();
          }
        });
    },
    [actions, bump],
  );

  const api = useMemo<ObjectSearchApi>(() => {
    const cancel = (objectId: string) => {
      const controller = controllers.current.get(objectId);
      if (!controller) return;
      controller.abort();
      controllers.current.delete(objectId);
      const obj = stateRef.current.objects.find((o) => o.id === objectId);
      if (obj?.search.status === "running") {
        actions.setSearchState(objectId, {
          status: obj.candidateIds.length > 0 ? "done" : "idle",
          lastRunAt: obj.search.lastRunAt,
        });
      }
      bump();
    };

    // Queued rather than run immediately: stateRef only refreshes after the next commit,
    // so a search fired alongside createObject/updateObject must wait for it.
    const search = (objectId: string) => {
      pending.current.add(objectId);
      bump();
    };

    const searchAll = (objectIds: string[]) => {
      const targets = objectIds.filter(
        (id) => stateRef.current.objects.find((o) => o.id === id)?.source === "search",
      );
      targets.forEach((id, i) => {
        const timer = setTimeout(() => {
          staggerTimers.current.delete(timer);
          search(id);
        }, i * STAGGER_MS);
        staggerTimers.current.add(timer);
      });
    };

    return {
      search,
      searchAll,
      cancel,
      isRunning: (objectId) => controllers.current.has(objectId),
    };
  }, [actions, bump]);

  useLayoutEffect(() => {
    if (pending.current.size === 0) return;
    const ids = [...pending.current];
    pending.current.clear();
    ids.forEach((id) => run(id));
  });

  useEffect(() => {
    const running = controllers.current;
    const timers = staggerTimers.current;
    const queued = pending.current;
    return () => {
      running.forEach((c) => c.abort());
      running.clear();
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
      queued.clear();
    };
  }, []);

  return <ObjectSearchContext.Provider value={api}>{children}</ObjectSearchContext.Provider>;
}

export function useObjectSearch(): ObjectSearchApi {
  const api = useContext(ObjectSearchContext);
  if (!api) throw new Error("useObjectSearch must be used inside ObjectSearchProvider");
  return api;
}
