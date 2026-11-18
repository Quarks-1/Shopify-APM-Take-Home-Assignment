import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createRender, describeError, fetchRender, isAbortError } from "@/lib/api";
import type { ApiRender } from "@/lib/api";
import { useAppState } from "@/state/app-store";
import { getObject, getSpace } from "@/state/selectors";
import { useStoreInternals } from "@/state/store-context";

const POLL_INTERVAL_MS = 1500;
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

function normalizeRender(render: ApiRender): ApiRender {
  if (!render.imageUrl || !render.imageUrl.startsWith("/") || !API_BASE) return render;
  return { ...render, imageUrl: `${API_BASE}${render.imageUrl}` };
}

interface ViewState {
  spaceId: string;
  render: ApiRender | null;
  error: string | null;
  layoutKey: string;
}

/**
 * State for the "Imagine view" modal of one space.
 *
 * `imagine(false)` reuses the stored image when the layout is unchanged (the
 * backend answers at once); `imagine(true)` forces a new generation. Renders
 * still running are polled until they finish. State is keyed by space, so a
 * different space starts empty.
 */
export function useImagineView(spaceId: string) {
  const state = useAppState();
  const { enqueue } = useStoreInternals();
  const [isOpen, setOpen] = useState(false);
  const [submittingSpaceId, setSubmittingSpaceId] = useState<string | null>(null);
  const [view, setView] = useState<ViewState>({ spaceId, render: null, error: null, layoutKey: "" });
  // Bumped by every imagine() call and on space change; responses from older calls are ignored.
  const requestRef = useRef(0);

  const space = getSpace(state, spaceId);
  const layoutKey = useMemo(() => {
    if (!space) return "";
    return JSON.stringify({
      type: space.type,
      unit: space.unit,
      dims: space.dims,
      placements: space.placements.map((p) => {
        const obj = getObject(state, p.objectId);
        return {
          id: p.objectId,
          x: p.x,
          y: p.y,
          name: obj?.name,
          type: obj?.type,
          dims: obj?.dims,
          selectedProductId: obj?.selectedProductId,
        };
      }),
    });
  }, [space, state]);

  const current = view.spaceId === spaceId ? view : { spaceId, render: null, error: null, layoutKey: "" };
  const render = current.render;
  const error = current.error;
  const isSubmitting = submittingSpaceId === spaceId;
  const runningId = render?.status === "running" ? render.id : null;

  // Abandon in-flight requests when the space changes or the component unmounts.
  useEffect(
    () => () => {
      requestRef.current += 1;
    },
    [spaceId],
  );

  // Poll the running render until it finishes. Re-runs when a new render starts, which aborts the old loop.
  useEffect(() => {
    if (!runningId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const { render: next } = await fetchRender(runningId, controller.signal);
        if (controller.signal.aborted) return;
        const normalized = normalizeRender(next);
        setView((prev) =>
          prev.spaceId !== spaceId || prev.render?.id !== runningId
            ? prev
            : { spaceId, render: normalized, error: null, layoutKey: prev.layoutKey },
        );
        if (normalized.status === "running") timer = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err) {
        if (controller.signal.aborted || isAbortError(err)) return;
        const message = err instanceof ApiError ? err.code : describeError(err);
        setView((prev) =>
          prev.spaceId !== spaceId || prev.render?.id !== runningId
            ? prev
            : { spaceId, render: null, error: message, layoutKey: prev.layoutKey },
        );
      }
    };

    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [runningId, spaceId]);

  const imagine = useCallback(
    async (force = false) => {
      if (isSubmitting || runningId !== null) {
        if (!force) setOpen(true);
        return;
      }
      const id = ++requestRef.current;
      setSubmittingSpaceId(spaceId);
      setOpen(true);
      // Keep the previous render only when !force and the layout hasn't changed; otherwise clear immediately so a stale image never flashes.
      setView((prev) => ({
        spaceId,
        render: !force && prev.spaceId === spaceId && prev.layoutKey === layoutKey ? prev.render : null,
        error: null,
        layoutKey,
      }));
      try {
        // enqueue waits for pending layout writes so the backend sees the latest positions.
        const result = await enqueue(() => createRender(spaceId, { force }));
        if (id !== requestRef.current) return;
        setView({ spaceId, render: normalizeRender(result.render), error: null, layoutKey });
      } catch (err) {
        if (id !== requestRef.current || isAbortError(err)) return;
        setView({ spaceId, render: null, error: err instanceof ApiError ? err.code : describeError(err), layoutKey });
      } finally {
        if (id === requestRef.current) setSubmittingSpaceId(null);
      }
    },
    [enqueue, isSubmitting, layoutKey, runningId, spaceId],
  );

  return { isOpen, setOpen, render, error, isSubmitting, imagine };
}
