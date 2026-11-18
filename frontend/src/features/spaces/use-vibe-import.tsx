import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "@/components/base/toast/toast";
import * as api from "@/lib/api";
import { ApiError, describeError, type ApiVibeImport } from "@/lib/api";
import { VIBE_IMPORT_STEPS, type VibeImportStep } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { useAppStatus, useStoreInternals } from "@/state/store-context";

/** What the status box shows for one space. */
export type VibeImportView =
  | { kind: "starting" }
  | { kind: "active"; imp: ApiVibeImport; step: VibeImportStep }
  | { kind: "done"; imp: ApiVibeImport }
  | { kind: "failed"; imp?: ApiVibeImport; message: string };

/** A view plus whether the user hid the running box (it comes back when the import finishes). */
export type VibeImportEntryView = VibeImportView & { hidden: boolean };

export interface VibeImportApi {
  /** Starts importing a (non-empty, already parsed) board link into the space. */
  start(spaceId: string, url: string): void;
  viewFor(spaceId: string): VibeImportEntryView | undefined;
  /** True while an import for the space is starting or running. */
  isActive(spaceId: string): boolean;
  /** Removes the status box and marks the import dismissed on the backend. */
  dismiss(spaceId: string): void;
  /** Hides the running box until the import finishes. */
  hide(spaceId: string): void;
  /** Picks up imports that are going (or finished unseen) without us: after a reload, or in another tab. */
  refresh(): void;
}

const VibeImportContext = createContext<VibeImportApi | null>(null);

const POLL_MS = 1500;
const QUEUED_POLL_MS = 3000;
/** Polling backs off to this while the backend keeps failing, and never gives up. */
const MAX_RETRY_MS = 10_000;

const GENERIC_FAILURE = "Something went wrong while importing your board.";

interface Entry {
  spaceId: string;
  view: VibeImportView;
  hidden: boolean;
  importId?: string;
  /** Highest step index shown for `importId`, so a stale poll never moves the step backwards. */
  highestStep: number;
  stopped: boolean;
  failures: number;
  timer?: ReturnType<typeof setTimeout>;
}

/**
 * Starts "vibes" imports and follows them, outside of page components so they
 * survive navigation. Render inside AppStoreProvider.
 */
export function VibeImportProvider({ children }: { children: ReactNode }) {
  const state = useAppState();
  const actions = useAppActions();
  const { status } = useAppStatus();
  const { getState, enqueue, onReset, onResetSettled, isResetting } = useStoreInternals();

  const entries = useRef(new Map<string, Entry>());
  // Bumped on every change so consumers re-render.
  const [version, setVersion] = useState(0);

  const engine = useMemo(() => {
    const bump = () => setVersion((v) => v + 1);
    // Bumped on every reset, so a refresh that was in flight can tell its answer predates it.
    let resetGeneration = 0;

    const remove = (spaceId: string) => {
      const entry = entries.current.get(spaceId);
      if (!entry) return;
      entry.stopped = true;
      if (entry.timer !== undefined) clearTimeout(entry.timer);
      entries.current.delete(spaceId);
      bump();
    };

    const removeAll = () => [...entries.current.keys()].forEach(remove);

    const handleReset = () => {
      resetGeneration += 1;
      removeAll();
    };

    const schedule = (entry: Entry, delay: number) => {
      if (entry.stopped) return;
      entry.timer = setTimeout(() => void poll(entry), delay);
    };

    const observe = (entry: Entry, imp: ApiVibeImport) => {
      if (entry.stopped) return;
      if (imp.boardId && (imp.status === "running" || imp.status === "done")) {
        actions.setSpaceVibeBoard(entry.spaceId, imp.boardId);
      }
      if (imp.dismissed) {
        remove(entry.spaceId);
        return;
      }
      if (entry.importId !== imp.id) entry.highestStep = 0;
      entry.importId = imp.id;
      if (imp.status === "done") {
        entry.view = { kind: "done", imp };
        entry.hidden = false;
        bump();
        return;
      }
      if (imp.status === "failed") {
        entry.view = { kind: "failed", imp, message: imp.error ?? GENERIC_FAILURE };
        entry.hidden = false;
        bump();
        return;
      }
      const reported = VIBE_IMPORT_STEPS.indexOf(imp.step ?? "reading-board");
      entry.highestStep = Math.max(entry.highestStep, reported, 0);
      entry.view = { kind: "active", imp, step: VIBE_IMPORT_STEPS[entry.highestStep] };
      bump();
      schedule(entry, imp.status === "queued" ? QUEUED_POLL_MS : POLL_MS);
    };

    const poll = async (entry: Entry) => {
      if (entry.stopped || !entry.importId) return;
      try {
        const imp = await api.getVibeImport(entry.importId);
        entry.failures = 0;
        observe(entry, imp);
      } catch (error) {
        if (entry.stopped) return;
        if (error instanceof ApiError && error.status === 404) {
          remove(entry.spaceId);
          return;
        }
        // A blip (backend restarting, network): keep the box as it is and try again.
        entry.failures += 1;
        schedule(entry, Math.min(POLL_MS * 2 ** entry.failures, MAX_RETRY_MS));
      }
    };

    const create = (spaceId: string, view: VibeImportView): Entry => {
      remove(spaceId);
      const entry: Entry = { spaceId, view, hidden: false, highestStep: 0, stopped: false, failures: 0 };
      entries.current.set(spaceId, entry);
      bump();
      return entry;
    };

    const start = (spaceId: string, url: string) => {
      const entry = create(spaceId, { kind: "starting" });
      // Queued behind pending writes, so a just-created space reaches the backend first.
      enqueue(() => api.startVibeImport(spaceId, url))
        .then((imp) => observe(entry, imp))
        .catch((error: unknown) => {
          if (entry.stopped) return;
          const active =
            error instanceof ApiError && error.status === 409
              ? (error.payload as { import?: ApiVibeImport } | undefined)?.import
              : undefined;
          if (active) {
            toast("A board is already importing", {
              description: "Your new link wasn't added. Try again once the current import finishes.",
            });
            observe(entry, active);
            return;
          }
          const message =
            error instanceof ApiError && error.details?.boardUrl ? error.details.boardUrl : describeError(error);
          entry.view = { kind: "failed", message };
          bump();
        });
    };

    const dismiss = (spaceId: string) => {
      const importId = entries.current.get(spaceId)?.importId;
      remove(spaceId);
      if (importId) api.dismissVibeImport(importId).catch(() => {});
    };

    const hide = (spaceId: string) => {
      const entry = entries.current.get(spaceId);
      if (!entry || entry.hidden) return;
      entry.hidden = true;
      bump();
    };

    const refresh = () => {
      // The backend is still being wiped: what it lists now is pre-reset. Settling refreshes again.
      if (isResetting()) return;
      const generation = resetGeneration;
      api
        .listPendingVibeImports()
        .then((imports) => {
          // A reset started while this was in flight; its list holds imports the reset deleted.
          if (generation !== resetGeneration) return;
          const spaceIds = new Set(getState().spaces.map((s) => s.id));
          for (const imp of imports) {
            if (imp.dismissed || !imp.spaceId || !spaceIds.has(imp.spaceId)) continue;
            const existing = entries.current.get(imp.spaceId);
            // Already followed (or being started right now): the tracker keeps its own pace.
            if (existing && (existing.importId === imp.id || existing.view.kind === "starting")) continue;
            observe(create(imp.spaceId, { kind: "starting" }), imp);
          }
        })
        .catch(() => {});
    };

    return { start, dismiss, hide, refresh, remove, removeAll, handleReset };
  }, [actions, enqueue, getState, isResetting]);

  // Pick up imports that are going without us once the store is loaded.
  useEffect(() => {
    if (status.phase === "ready") engine.refresh();
  }, [status.phase, engine]);

  // Drop trackers whose space is gone.
  useEffect(() => {
    if (status.phase !== "ready") return;
    const ids = new Set(state.spaces.map((s) => s.id));
    for (const id of [...entries.current.keys()]) if (!ids.has(id)) engine.remove(id);
  }, [state.spaces, status.phase, engine]);

  useEffect(() => onReset(engine.handleReset), [onReset, engine]);

  // Once the backend wipe finishes, pick up imports from its real post-reset state.
  useEffect(() => onResetSettled(engine.refresh), [onResetSettled, engine]);

  useEffect(() => engine.removeAll, [engine]);

  const value = useMemo<VibeImportApi>(
    () => ({
      start: engine.start,
      viewFor: (spaceId) => {
        const entry = entries.current.get(spaceId);
        return entry ? { ...entry.view, hidden: entry.hidden } : undefined;
      },
      isActive: (spaceId) => {
        const kind = entries.current.get(spaceId)?.view.kind;
        return kind === "starting" || kind === "active";
      },
      dismiss: engine.dismiss,
      hide: engine.hide,
      refresh: engine.refresh,
    }),
    // `version` makes viewFor/isActive consumers re-render when an import changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [engine, version],
  );

  return <VibeImportContext.Provider value={value}>{children}</VibeImportContext.Provider>;
}

export function useVibeImport(): VibeImportApi {
  const api = useContext(VibeImportContext);
  if (!api) throw new Error("useVibeImport must be used inside VibeImportProvider");
  return api;
}
