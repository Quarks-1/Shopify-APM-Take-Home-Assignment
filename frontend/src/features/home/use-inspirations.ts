import { useCallback, useEffect, useMemo, useState } from "react";
import { listInspirations, type ApiInspiration } from "@/lib/api";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { useAppState } from "@/state/app-store";
import { useStoreInternals } from "@/state/store-context";

export interface InspirationsResult {
  status: "loading" | "ready" | "error";
  inspirations: ApiInspiration[];
  retry(): void;
}

/** Every imported pin. Refetches when spaces change or a vibe import finishes (done or failed); keeps old data meanwhile. */
export function useInspirations(): InspirationsResult {
  const { spaces } = useAppState();
  const { viewFor } = useVibeImport();
  const { onReset, onResetSettled, isResetting } = useStoreInternals();
  const [data, setData] = useState<ApiInspiration[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // Bumped when a reset starts and when it settles, to abort and re-run the fetch effect.
  const [resetEpoch, setResetEpoch] = useState(0);

  const spacesKey = spaces
    .map((s) => s.id)
    .sort()
    .join(",");
  const doneKey = spaces
    .map((s) => {
      const view = viewFor(s.id);
      // A failed import (e.g. a failed sort) may already have replaced the old pins.
      if (view?.kind === "done") return view.imp.id;
      return view?.kind === "failed" && view.imp ? view.imp.id : "";
    })
    .filter(Boolean)
    .sort()
    .join(",");

  useEffect(() => {
    // The backend is mid-reset and would answer with the old pins; the settle refetch below covers it.
    if (isResetting()) return;
    const controller = new AbortController();
    listInspirations(controller.signal)
      .then((list) => {
        if (controller.signal.aborted) return;
        setData(list);
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [spacesKey, doneKey, attempt, resetEpoch, isResetting]);

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
  }, [onReset, onResetSettled]);

  const retry = useCallback(() => {
    setFailed(false);
    setAttempt((n) => n + 1);
  }, []);

  return useMemo(
    () => ({
      status: data ? "ready" : failed ? "error" : "loading",
      inspirations: data ?? [],
      retry,
    }),
    [data, failed, retry],
  );
}
