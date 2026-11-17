import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/components/base/toast/toast";
import { ApiError, deleteSpaceInspiration, listSpaceInspirations, type ApiInspiration } from "@/lib/api";
import { useVibeImport } from "@/features/spaces/use-vibe-import";

export interface SpaceInspirationsResult {
  status: "loading" | "ready" | "error";
  /** Sorted (categorized) pins only, in board order. */
  inspirations: ApiInspiration[];
  remove(id: number): void;
  retry(): void;
}

/** One space's categorized pins. Refetches when its vibe import finishes (done or failed); keeps old data meanwhile. */
export function useSpaceInspirations(spaceId: string): SpaceInspirationsResult {
  const { viewFor } = useVibeImport();
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
  const dataRef = useRef<ApiInspiration[] | null>(null);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  const view = viewFor(spaceId);
  const doneKey = view?.kind === "done" ? view.imp.id : view?.kind === "failed" && view.imp ? view.imp.id : "";

  useEffect(() => {
    const controller = new AbortController();
    listSpaceInspirations(spaceId, controller.signal)
      .then((list) => {
        setData(list.filter((p) => p.categorized));
        setFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [spaceId, doneKey, attempt, setData]);

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
