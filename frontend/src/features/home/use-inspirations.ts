import { useCallback, useEffect, useMemo, useState } from "react";
import {
  listInspirations,
  type ApiInspiration,
  type ApiInspirationBoard,
  type ApiInspirationsPayload,
} from "@/lib/api";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { useAppState } from "@/state/app-store";
import { useStoreInternals } from "@/state/store-context";

export interface InspirationsResult {
  status: "loading" | "ready" | "error";
  boards: ApiInspirationBoard[];
  inspirations: ApiInspiration[];
  retry(): void;
}

const EMPTY_PAYLOAD: ApiInspirationsPayload = { boards: [], inspirations: [] };

/** Imported boards and their sorted pins. Refetches when spaces or vibe imports settle; keeps old data meanwhile. */
export function useInspirations(): InspirationsResult {
  const { spaces } = useAppState();
  const { viewFor } = useVibeImport();
  const { enqueue, onReset, onResetSettled, isResetting } = useStoreInternals();
  const [data, setData] = useState<ApiInspirationsPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // Bumped when a reset starts and when it settles, to abort and re-run the fetch effect.
  const [resetEpoch, setResetEpoch] = useState(0);

  const spacesKey = spaces
    .map((s) => `${s.id}:${s.vibeBoardId ?? ""}`)
    .sort()
    .join(",");
  const doneKey = spaces
    .map((s) => {
      const view = viewFor(s.id);
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
    enqueue(async () => {})
      .then(() => {
        if (controller.signal.aborted) return;
        return listInspirations(controller.signal);
      })
      .then((payload) => {
        if (!payload || controller.signal.aborted) return;
        setData(payload);
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [spacesKey, doneKey, attempt, resetEpoch, isResetting, enqueue]);

  // Reset demo data: the seed has no boards, so show an empty list at once. Any in-flight fetch is aborted
  // (the epoch bump re-runs the effect, which skips fetching while the reset is pending); once the backend
  // has finished, refetch so the list reflects its real state.
  useEffect(() => {
    const offReset = onReset(() => {
      setData(EMPTY_PAYLOAD);
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
      boards: data?.boards ?? [],
      inspirations: data?.inspirations ?? [],
      retry,
    }),
    [data, failed, retry],
  );
}
