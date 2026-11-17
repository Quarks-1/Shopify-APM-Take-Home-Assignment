import { useCallback, useEffect, useMemo, useState } from "react";
import { listInspirations, type ApiInspiration } from "@/lib/api";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { useAppState } from "@/state/app-store";

export interface InspirationsResult {
  status: "loading" | "ready" | "error";
  inspirations: ApiInspiration[];
  retry(): void;
}

/** Every imported pin. Refetches when spaces change or a vibe import finishes; keeps old data meanwhile. */
export function useInspirations(): InspirationsResult {
  const { spaces } = useAppState();
  const { viewFor } = useVibeImport();
  const [data, setData] = useState<ApiInspiration[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const spacesKey = spaces
    .map((s) => s.id)
    .sort()
    .join(",");
  const doneKey = spaces
    .map((s) => {
      const view = viewFor(s.id);
      return view?.kind === "done" ? view.imp.id : "";
    })
    .filter(Boolean)
    .sort()
    .join(",");

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
  }, [spacesKey, doneKey, attempt]);

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
