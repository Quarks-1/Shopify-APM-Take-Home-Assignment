import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, RefObject } from "react";
import { useDirection } from "@/components/foundations/direction/direction";
import { cx } from "@/utils/cx";

const STORAGE_KEY = "spaces.editor.split";
const DEFAULT_SPLIT = 1.2 / 2.2;
/** Width of the handle column (1.5rem), in rem. */
const HANDLE_REM = 1.5;
const START_MIN_REM = 18;
const END_MIN_REM = 20;
const STEP = 0.02;
const STEP_LARGE = 0.1;

function remPx(): number {
  const size = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(size) && size > 0 ? size : 16;
}

function readStored(): number {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_SPLIT;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 && value < 1 ? value : DEFAULT_SPLIT;
  } catch {
    return DEFAULT_SPLIT;
  }
}

function writeStored(value: number) {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    /* storage unavailable; the split just won't persist */
  }
}

export interface PanelSplit {
  /** Start column's share of the row (0..1). */
  split: number;
  setSplit: (value: number) => void;
  /** Current split, read synchronously (not stale between renders). */
  get: () => number;
  /** Persist the current split to localStorage. */
  commit: (value?: number) => void;
  reset: () => void;
  /** Allowed range for the split given the current container width. */
  bounds: () => { min: number; max: number };
  containerRef: RefObject<HTMLDivElement | null>;
  /** Measured container width in px (0 until measured). */
  containerWidth: number;
}

export function usePanelSplit(): PanelSplit {
  const [split, setSplitState] = useState<number>(readStored);
  const [containerWidth, setContainerWidth] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const splitRef = useRef(split);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setContainerWidth(el.getBoundingClientRect().width);
    const observer = new ResizeObserver(() => setContainerWidth(el.getBoundingClientRect().width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const bounds = useCallback(() => {
    const el = containerRef.current;
    if (!el) return { min: 0, max: 1 };
    const rem = remPx();
    const available = el.getBoundingClientRect().width - HANDLE_REM * rem;
    if (available <= 0) return { min: 0, max: 1 };
    const min = (START_MIN_REM * rem) / available;
    const max = 1 - (END_MIN_REM * rem) / available;
    // If the container is too narrow for both minimums, fall back to the default.
    if (min >= max) return { min: DEFAULT_SPLIT, max: DEFAULT_SPLIT };
    return { min: Math.max(0, min), max: Math.min(1, max) };
  }, []);

  const setSplit = useCallback(
    (value: number) => {
      const { min, max } = bounds();
      const next = Math.min(max, Math.max(min, Math.min(1, Math.max(0, value))));
      splitRef.current = next;
      setSplitState(next);
    },
    [bounds],
  );

  const get = useCallback(() => splitRef.current, []);

  const commit = useCallback((value?: number) => writeStored(value ?? splitRef.current), []);

  const reset = useCallback(() => {
    splitRef.current = DEFAULT_SPLIT;
    setSplitState(DEFAULT_SPLIT);
    writeStored(DEFAULT_SPLIT);
  }, []);

  return { split, setSplit, get, commit, reset, bounds, containerRef, containerWidth };
}

interface PanelResizerProps {
  panelSplit: PanelSplit;
  /** Id of the element (the start panel) this handle resizes. */
  controlsId: string;
  className?: string;
}

export function PanelResizer({ panelSplit, controlsId, className }: PanelResizerProps) {
  const { split, setSplit, get, commit, reset, bounds, containerRef, containerWidth } = panelSplit;
  const rtl = useDirection() === "rtl";
  const [dragging, setDragging] = useState(false);

  // Cursor + selection lock while dragging; always restored on release/unmount.
  useEffect(() => {
    if (!dragging) return;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = cursor;
      document.body.style.userSelect = userSelect;
    };
  }, [dragging]);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    e.preventDefault();
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const handle = HANDLE_REM * remPx();
    const available = rect.width - handle;
    if (available <= 0) return;
    const offset = rtl ? rect.right - e.clientX : e.clientX - rect.left;
    setSplit((offset - handle / 2) / available);
  };

  const endDrag = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
    commit();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? STEP_LARGE : STEP;
    // ArrowRight grows the start column in LTR; ArrowLeft grows it in RTL.
    const grow = rtl ? "ArrowLeft" : "ArrowRight";
    const shrink = rtl ? "ArrowRight" : "ArrowLeft";
    let next: number | null = null;
    if (e.key === grow) next = get() + step;
    else if (e.key === shrink) next = get() - step;
    else if (e.key === "Home") next = bounds().min;
    else if (e.key === "End") next = bounds().max;
    else if (e.key === "Enter") {
      e.preventDefault();
      reset();
      return;
    } else return;
    e.preventDefault();
    setSplit(next);
    commit();
  };

  // Bounds in percent for assistive tech; fall back to a sane range until measured.
  const range = containerWidth > 0 ? bounds() : { min: 0, max: 1 };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize plan and objects panels"
      aria-controls={controlsId}
      aria-valuenow={Math.round(split * 100)}
      aria-valuemin={Math.ceil(range.min * 100)}
      aria-valuemax={Math.floor(range.max * 100)}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={() => setDragging(false)}
      onDoubleClick={reset}
      onKeyDown={onKeyDown}
      data-dragging={dragging || undefined}
      className={cx(
        "group hidden cursor-col-resize touch-none items-center justify-center self-stretch outline-none",
        "lg:flex lg:h-auto",
        className,
      )}
    >
      <span
        aria-hidden
        className={cx(
          "h-12 w-1 rounded-full bg-border-button-default transition-colors",
          "group-hover:bg-accent-500 group-data-[dragging]:bg-accent-500",
          "group-focus-visible:bg-accent-500 group-focus-visible:ring-2 group-focus-visible:ring-border-focus-ring",
        )}
      />
    </div>
  );
}
