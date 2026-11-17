import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, createRender, describeError, fetchRender, isAbortError } from "@/lib/api";
import type { ApiRender } from "@/lib/api";
import { useStoreInternals } from "@/state/store-context";

const POLL_INTERVAL_MS = 1500;

interface ViewState {
  spaceId: string;
  render: ApiRender | null;
  error: string | null;
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
  const { enqueue } = useStoreInternals();
  const [isOpen, setOpen] = useState(false);
  const [view, setView] = useState<ViewState>({ spaceId, render: null, error: null });
  // Bumped by every imagine() call and on space change; responses from older calls are ignored.
  const requestRef = useRef(0);

  const current = view.spaceId === spaceId ? view : { spaceId, render: null, error: null };
  const render = current.render;
  const error = current.error;
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
        setView((prev) =>
          prev.spaceId !== spaceId || prev.render?.id !== runningId ? prev : { spaceId, render: next, error: null },
        );
        if (next.status === "running") timer = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err) {
        if (controller.signal.aborted || isAbortError(err)) return;
        const message = err instanceof ApiError ? err.code : describeError(err);
        setView((prev) => (prev.spaceId !== spaceId || prev.render?.id !== runningId ? prev : { spaceId, render: null, error: message }));
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
      const id = ++requestRef.current;
      setOpen(true);
      // The previous render stays on screen until the response says what to show, so an unchanged layout re-views without flashing.
      setView((prev) => ({ spaceId, render: prev.spaceId === spaceId ? prev.render : null, error: null }));
      try {
        // enqueue waits for pending layout writes so the backend sees the latest positions.
        const result = await enqueue(() => createRender(spaceId, { force }));
        if (id !== requestRef.current) return;
        setView({ spaceId, render: result.render, error: null });
      } catch (err) {
        if (id !== requestRef.current || isAbortError(err)) return;
        setView({ spaceId, render: null, error: err instanceof ApiError ? err.code : describeError(err) });
      }
    },
    [enqueue, spaceId],
  );

  return { isOpen, setOpen, render, error, imagine };
}
