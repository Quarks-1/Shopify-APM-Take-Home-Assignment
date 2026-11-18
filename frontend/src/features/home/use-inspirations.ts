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
  const { onReset } = useStoreInternals();
  const [data, setData] = useState<ApiInspiration[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const spacesKey = spaces
    .map((s) => s.id)
    .sort()
    .join(",");
  const doneIds = spaces
    .map((s) => {
      const view = viewFor(s.id);
      // A failed import (e.g. a failed sort) may already have replaced the old pins.
      if (view?.kind === "done") return view.imp.id;
      return view?.kind === "failed" && view.imp ? view.imp.id : "";
    })
    .filter(Boolean)
    .sort();
  // Accumulate seen finished import ids so auto-dismissing a pill never triggers a redundant refetch.
  const [seenDoneKey, setSeenDoneKey] = useState<string>(() => doneIds.join(","));
  const knownIds = seenDoneKey === "" ? [] : seenDoneKey.split(",");
  if (doneIds.some((id) => !knownIds.includes(id))) {
    setSeenDoneKey([...new Set([...knownIds, ...doneIds])].sort().join(","));
  }

  useEffect(() => {
    const controller = new AbortController();
    listInspirations(controller.signal)
      .then((list) => {
        setData(list);
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [spacesKey, seenDoneKey, attempt]);

  // Reset demo data: the seed has no boards, so show an empty list at once (no refetch; the backend is mid-reset).
  useEffect(
    () =>
      onReset(() => {
        setSeenDoneKey("");
        setData([]);
        setFailed(false);
      }),
    [onReset],
  );

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
