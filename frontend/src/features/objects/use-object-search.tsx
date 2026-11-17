import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { toast } from "@/components/base/toast/toast";
import * as api from "@/lib/api";
import { ACTIVE_RUN_STATUSES, ApiError, describeError, type ApiSearchRun } from "@/lib/api";
import { SEARCH_STEPS } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { useAppStatus, useStoreInternals } from "@/state/store-context";

export interface ObjectSearchApi {
  search(objectId: string): void;
  searchAll(objectIds: string[]): void;
  cancel(objectId: string): void;
  isRunning(objectId: string): boolean;
}

const ObjectSearchContext = createContext<ObjectSearchApi | null>(null);

const POLL_MS = 1500;
/** Polling backs off to this while the backend keeps failing, and never gives up. */
const MAX_RETRY_MS = 10_000;
/** Fast runs would skip "building-query"; a user-started search keeps it on screen at least this long. */
const STEP_2_MIN_MS = 3000;

/** One object's run being followed. Without `runId` the run is still being started or looked up. */
interface Tracker {
  objectId: string;
  runId?: string;
  /** Highest step shown for `runId`, so a stale poll never moves the step backwards. */
  highestStep?: number;
  /** Show "building-query" for STEP_2_MIN_MS (searches the user started, not resumed ones). */
  holdStep2: boolean;
  /** When "building-query" went on screen for `runId`. */
  step2Since?: number;
  stopped: boolean;
  /** Cancel was pressed before the start request returned a run id. */
  cancelRequested: boolean;
  failures: number;
  timer?: ReturnType<typeof setTimeout>;
}

/**
 * Starts backend search runs and polls them, outside of page components so
 * they survive navigation. Runs already going (after a reload, or started in
 * another tab) are picked up from the object's search state. Render inside
 * AppStoreProvider.
 */
export function ObjectSearchProvider({ children }: { children: ReactNode }) {
  const state = useAppState();
  const actions = useAppActions();
  const { status } = useAppStatus();
  const { getState, enqueue, onReset } = useStoreInternals();

  const trackers = useRef(new Map<string, Tracker>());
  // Bumped whenever a run starts or ends so isRunning consumers re-render.
  const [version, setVersion] = useState(0);

  const engine = useMemo(() => {
    const bump = () => setVersion((v) => v + 1);
    const findObject = (objectId: string) => getState().objects.find((o) => o.id === objectId);

    /** Leaves search state as it was before the run: done when there are matches to show. */
    const settleIdle = (objectId: string, lastRunAt?: string | null) => {
      const obj = findObject(objectId);
      if (!obj || obj.search.status !== "running") return;
      actions.setSearchState(objectId, {
        status: obj.candidateIds.length > 0 ? "done" : "idle",
        lastRunAt: lastRunAt ?? obj.search.lastRunAt,
      });
    };

    const stop = (objectId: string) => {
      const tracker = trackers.current.get(objectId);
      if (!tracker) return;
      tracker.stopped = true;
      if (tracker.timer !== undefined) clearTimeout(tracker.timer);
      trackers.current.delete(objectId);
      bump();
    };

    const stopAll = () => [...trackers.current.keys()].forEach(stop);

    const finish = (tracker: Tracker, run: ApiSearchRun) => {
      const { objectId } = tracker;
      stop(objectId);
      const obj = findObject(objectId);
      if (!obj) return;
      switch (run.status) {
        case "done":
        case "empty":
          // Results of any kind clear the pick; an empty run shows "No matches". The user chooses a card.
          actions.applySearchResults(objectId, run.status === "done" ? run.candidates : [], run.finishedAt);
          break;
        case "failed":
          toast.error(`Search failed for ${obj.name}`, {
            description: run.error ?? "The searcher stopped before finding matches.",
          });
          actions.setSearchState(objectId, { status: "idle", lastRunAt: run.finishedAt ?? obj.search.lastRunAt });
          break;
        case "canceled":
          settleIdle(objectId, run.finishedAt);
          break;
      }
    };

    const schedule = (tracker: Tracker, delay: number) => {
      if (tracker.stopped) return;
      tracker.timer = setTimeout(() => void poll(tracker), delay);
    };

    const observe = (tracker: Tracker, run: ApiSearchRun) => {
      if (tracker.stopped) return;
      if (tracker.runId !== run.id) {
        tracker.highestStep = undefined;
        tracker.step2Since = undefined;
      }
      tracker.runId = run.id;
      if (tracker.holdStep2 && (tracker.step2Since === undefined || Date.now() - tracker.step2Since < STEP_2_MIN_MS)) {
        const held = findObject(tracker.objectId);
        if (!held) {
          stop(tracker.objectId);
          return;
        }
        if (tracker.step2Since === undefined) {
          tracker.step2Since = Date.now();
          tracker.highestStep = Math.max(tracker.highestStep ?? 0, 1);
          if (held.search.status !== "running" || held.search.step !== "building-query") {
            actions.setSearchState(tracker.objectId, { status: "running", step: "building-query", lastRunAt: held.search.lastRunAt });
          }
        }
        const remaining = STEP_2_MIN_MS - (Date.now() - tracker.step2Since);
        const pastStep2 = SEARCH_STEPS.indexOf(run.step ?? "planning") > 1 || !ACTIVE_RUN_STATUSES.has(run.status);
        if (remaining > 0 && pastStep2) {
          // Hold the later step or result back; the timer is cleared by stop() on cancel.
          tracker.timer = setTimeout(() => observe(tracker, run), remaining);
          return;
        }
      }
      if (!ACTIVE_RUN_STATUSES.has(run.status)) {
        finish(tracker, run);
        return;
      }
      const obj = findObject(tracker.objectId);
      if (!obj) {
        stop(tracker.objectId);
        return;
      }
      const reported = SEARCH_STEPS.indexOf(run.step ?? "planning");
      const highest = Math.max(reported, tracker.highestStep ?? 0);
      tracker.highestStep = highest;
      const step = SEARCH_STEPS[highest];
      if (obj.search.status !== "running" || obj.search.step !== step) {
        actions.setSearchState(tracker.objectId, { status: "running", step, lastRunAt: obj.search.lastRunAt });
      }
      // Queued runs wait for a free slot (two run at once), so check on them less often.
      schedule(tracker, run.status === "queued" ? POLL_MS * 2 : POLL_MS);
    };

    const poll = async (tracker: Tracker) => {
      if (tracker.stopped) return;
      try {
        const run = tracker.runId
          ? await api.getSearch(tracker.runId)
          : await api.getLatestSearch(tracker.objectId);
        tracker.failures = 0;
        observe(tracker, run);
      } catch (error) {
        if (tracker.stopped) return;
        if (error instanceof ApiError && error.status === 404) {
          // The run (or the whole object) is gone: deleted, or reset in another tab.
          stop(tracker.objectId);
          settleIdle(tracker.objectId);
          return;
        }
        // A blip (backend restarting, network): keep the run's UI and try again.
        tracker.failures += 1;
        schedule(tracker, Math.min(POLL_MS * 2 ** tracker.failures, MAX_RETRY_MS));
      }
    };

    const track = (objectId: string, holdStep2 = false): Tracker => {
      const tracker: Tracker = { objectId, holdStep2, stopped: false, cancelRequested: false, failures: 0 };
      trackers.current.set(objectId, tracker);
      bump();
      return tracker;
    };

    const search = (objectId: string) => {
      const obj = findObject(objectId);
      // A second press while running is a no-op; the backend would hand back the same run anyway.
      if (!obj || obj.source !== "search" || trackers.current.has(objectId)) return;
      const tracker = track(objectId, true);
      actions.setSearchState(objectId, { status: "running", step: "planning", lastRunAt: obj.search.lastRunAt });
      // Queued behind pending writes, so a just-created object reaches the backend first.
      enqueue(() => api.startSearch(objectId))
        .then((run) => {
          if (tracker.cancelRequested) {
            // A newer search for this object gets the same active run back, so cancelling it would kill that one.
            if (!trackers.current.has(objectId) && ACTIVE_RUN_STATUSES.has(run.status)) void enqueue(() => api.cancelSearch(run.id)).catch(() => {});
            return;
          }
          observe(tracker, run);
        })
        .catch((error: unknown) => {
          if (tracker.stopped) return;
          stop(objectId);
          settleIdle(objectId);
          toast.error(`Couldn't start the search for ${obj.name}`, { description: describeError(error) });
        });
    };

    const resume = (objectId: string) => {
      if (trackers.current.has(objectId)) return;
      void poll(track(objectId));
    };

    const cancel = (objectId: string) => {
      const tracker = trackers.current.get(objectId);
      const obj = findObject(objectId);
      if (tracker) {
        tracker.cancelRequested = true;
        const { runId } = tracker;
        stop(objectId);
        if (runId) {
          enqueue(() => api.cancelSearch(runId)).catch((error: unknown) => {
            toast.error("Couldn't cancel the search", { description: describeError(error) });
          });
        }
      } else if (obj?.search.status === "running") {
        // Shown as running but not followed yet (e.g. a refresh just arrived).
        enqueue(async () => {
          const run = await api.getLatestSearch(objectId);
          if (ACTIVE_RUN_STATUSES.has(run.status)) await api.cancelSearch(run.id);
        }).catch(() => {});
      }
      settleIdle(objectId);
    };

    return { search, resume, cancel, stop, stopAll };
  }, [actions, enqueue, getState]);

  // Follow runs that are going without us (after a reload, from another tab), and drop deleted objects.
  useEffect(() => {
    if (status.phase !== "ready") return;
    for (const o of state.objects) {
      if (o.source === "search" && o.search.status === "running") engine.resume(o.id);
    }
    const ids = new Set(state.objects.map((o) => o.id));
    for (const id of [...trackers.current.keys()]) if (!ids.has(id)) engine.stop(id);
  }, [state.objects, status.phase, engine]);

  // Reset replaces every object (same ids, new records), so old runs no longer apply.
  useEffect(() => onReset(engine.stopAll), [onReset, engine]);

  useEffect(() => engine.stopAll, [engine]);

  const searchApi = useMemo<ObjectSearchApi>(
    () => ({
      search: engine.search,
      // The backend queues runs (two at a time), so they can all start at once.
      searchAll: (objectIds) => objectIds.forEach(engine.search),
      cancel: engine.cancel,
      isRunning: (objectId) => trackers.current.has(objectId),
    }),
    // `version` makes isRunning consumers re-render when a run starts or ends.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [engine, version],
  );

  return <ObjectSearchContext.Provider value={searchApi}>{children}</ObjectSearchContext.Provider>;
}

export function useObjectSearch(): ObjectSearchApi {
  const api = useContext(ObjectSearchContext);
  if (!api) throw new Error("useObjectSearch must be used inside ObjectSearchProvider");
  return api;
}
