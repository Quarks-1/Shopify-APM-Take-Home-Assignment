import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/components/base/toast/toast";
import { ApiError, deleteSpaceInspiration, listSpaceInspirations, type ApiInspiration } from "@/lib/api";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { useAppState } from "@/state/app-store";
import { getSpace } from "@/state/selectors";
import { useStoreInternals } from "@/state/store-context";

export interface SpaceInspirationsResult {
  status: "loading" | "ready" | "error";
  /** Sorted (categorized) pins only, in board order. */
  inspirations: ApiInspiration[];
  remove(id: number): void;
  retry(): void;
}

/** One space's categorized pins (from its chosen board). Refetches when its chosen board or import changes. */
export function useSpaceInspirations(spaceId: string): SpaceInspirationsResult {
  const state = useAppState();
  const { viewFor } = useVibeImport();
  const { enqueue, onReset, onResetSettled, isResetting } = useStoreInternals();
  const [loaded, setLoaded] = useState<{ spaceId: string; list: ApiInspiration[] } | null>(null);
  // Data from a previous space never shows for this one.
  const data = loaded?.spaceId === spaceId ? loaded.list : null;
  const setData = useCallback(
    (update: ApiInspiration[] | ((cur: ApiInspiration[] | null) => ApiInspiration[] | null)) =>
      setLoaded((cur) => {
        const prev = cur?.spaceId === spaceId ? cur.list : null;
        const list = typeof update === "function" ? update(prev) : update;
        return list ? { spaceId, list } : null;
      }),
    [spaceId],
  );
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // Bumped when a reset starts and when it settles, to abort and re-run the fetch effect.
  const [resetEpoch, setResetEpoch] = useState(0);
  const dataRef = useRef<ApiInspiration[] | null>(null);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  const vibeBoardId = getSpace(state, spaceId)?.vibeBoardId ?? "";
  const view = viewFor(spaceId);
  const doneKey = view?.kind === "done" ? view.imp.id : view?.kind === "failed" && view.imp ? view.imp.id : "";

  useEffect(() => {
    // The backend is mid-reset and would answer with the old pins; the settle refetch below covers it.
    if (isResetting()) return;
    const controller = new AbortController();
    // Wait for pending space writes (such as selecting a board in Space settings) before reading.
    enqueue(async () => {})
      .then(() => {
        if (controller.signal.aborted) return;
        return listSpaceInspirations(spaceId, controller.signal);
      })
      .then((list) => {
        if (!list || controller.signal.aborted) return;
        setData(list.filter((p) => p.categorized));
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [spaceId, vibeBoardId, doneKey, attempt, resetEpoch, isResetting, enqueue, setData]);

  // Reset demo data: the seed has no boards, so show an empty list at once. Any in-flight fetch is aborted
  // (the epoch bump re-runs the effect, which skips fetching while the reset is pending); once the backend
  // has finished, refetch so the list reflects its real state.
  useEffect(() => {
    const offReset = onReset(() => {
      setData([]);
      setFailed(false);
      setResetEpoch((n) => n + 1);
    });
    const offSettled = onResetSettled(() => setResetEpoch((n) => n + 1));
    return () => {
      offReset();
      offSettled();
    };
  }, [onReset, onResetSettled, setData]);

  const retry = useCallback(() => {
    setFailed(false);
    setAttempt((n) => n + 1);
  }, []);

  const remove = useCallback(
    (id: number) => {
      const before = dataRef.current;
      const index = before?.findIndex((p) => p.id === id) ?? -1;
      if (!before || index < 0) return;
      const pin = before[index];
      setData(before.filter((p) => p.id !== id));
      deleteSpaceInspiration(spaceId, id).catch((err: unknown) => {
        setData((current) => {
          if (!current || current.some((p) => p.id === id)) return current;
          const next = [...current];
          next.splice(Math.min(index, next.length), 0, pin);
          return next;
        });
        if (err instanceof ApiError && err.status === 409) toast.error("Wait for the import to finish");
        else toast.error("Couldn't remove that pin", { description: "Try again in a moment." });
      });
    },
    [spaceId, setData],
  );

  return useMemo(
    () => ({
      status: data ? "ready" : failed ? "error" : "loading",
      inspirations: data ?? [],
      remove,
      retry,
    }),
    [data, failed, remove, retry],
  );
}
