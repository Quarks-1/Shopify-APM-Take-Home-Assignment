import { useCallback, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { RiAddLine, RiCursorLine, RiShape2Line } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Kbd } from "@/components/base/kbd/kbd";
import { Meter } from "@/components/base/meter/meter";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { toast } from "@/components/base/toast/toast";
import { OBJECT_TYPE_SVG } from "@/features/objects/object-type-visuals";
import { useObjectDrawer } from "@/features/objects/use-object-drawer";
import { formatDims, formatLength, formatPrice, toDisplayLength } from "@/lib/format";
import type { Dims, Space } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import {
  footprint,
  getObject,
  getOverlappingObjectIds,
  getSpace,
  getSpaceEstimatedTotal,
  getSpaceWeight,
} from "@/state/selectors";
import { cx } from "@/utils/cx";

/* ------------------------------------------------------------------ helpers */

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Mode = "select" | "draw";

const IN_STEPS = [1, 2, 3, 6, 12, 24];
const CM_STEPS = [2, 5, 10, 20, 50];
const CM_PER_INCH = 2.54;
const MAX_CELLS = 40;

/** Visible grid-line step in inches: the smallest candidate that keeps the long side under 40 cells. */
function pickGridStep(dims: Dims, unit: Space["unit"]): number {
  const longest = Math.max(dims.x, dims.y);
  const candidates = unit === "cm" ? CM_STEPS.map((cm) => cm / CM_PER_INCH) : IN_STEPS;
  return candidates.find((step) => longest / step <= MAX_CELLS) ?? candidates[candidates.length - 1];
}

/** Interaction snap step in inches: 1 inch, or 1 cm when the space is measured in cm. */
const snapStepFor = (unit: Space["unit"]) => (unit === "cm" ? 1 / CM_PER_INCH : 1);

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));
const round4 = (n: number) => Math.round(n * 10000) / 10000;
const snap = (value: number, step: number) => round4(Math.round(value / step) * step);

/** Client coordinates to SVG user units (inches); handles letterboxing via the CTM. */
function toSvgPoint(svg: SVGSVGElement, clientX: number, clientY: number): { x: number; y: number } | null {
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

/** Object dims for a footprint on this space's plane (wall: x and z, otherwise x and y). */
function dimsForFootprint(space: Space, w: number, h: number, step: number): Dims {
  return space.type === "wall" ? { x: w, y: step, z: h } : { x: w, y: h };
}

type Drag =
  | { kind: "move" | "resize"; id: string; pointerId: number; start: { x: number; y: number }; origin: Rect; rect: Rect }
  | { kind: "draw"; pointerId: number; start: { x: number; y: number }; rect: Rect };

/* ---------------------------------------------------------------- component */

export interface SpaceDesignerProps {
  spaceId: string;
  selectedObjectId: string | null;
  onSelectObject: (id: string | null) => void;
  className?: string;
}

export function SpaceDesigner({ spaceId, selectedObjectId, onSelectObject, className }: SpaceDesignerProps) {
  const state = useAppState();
  const actions = useAppActions();
  const drawer = useObjectDrawer();
  const uid = useId();

  const svgRef = useRef<SVGSVGElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [drag, setDragState] = useState<Drag | null>(null);
  const [mode, setMode] = useState<Mode>(() =>
    (getSpace(state, spaceId)?.placements.length ?? 0) === 0 ? "draw" : "select",
  );

  const setDrag = useCallback((next: Drag | null) => {
    dragRef.current = next;
    setDragState(next);
  }, []);

  const space = getSpace(state, spaceId);
  const overlapping = useMemo(() => getOverlappingObjectIds(state, spaceId), [state, spaceId]);

  if (!space) return null;

  const { dims, unit } = space;
  const step = pickGridStep(dims, unit);
  const snapStep = snapStepFor(unit);
  const longest = Math.max(dims.x, dims.y);
  const pad = longest * 0.03;
  const fontSize = longest / 45;
  const handleSize = longest / 45;

  const items = space.placements.flatMap((p) => {
    const object = getObject(state, p.objectId);
    if (!object) return [];
    const size = footprint(object, space);
    let rect: Rect = { x: p.x, y: p.y, w: size.w, h: size.h };
    if (drag && drag.kind !== "draw" && drag.id === object.id) rect = drag.rect;
    return [{ object, placement: p, rect }];
  });

  const weight = getSpaceWeight(state, spaceId);
  const total = getSpaceEstimatedTotal(state, spaceId);
  const weightRatio = space.weightLimit ? weight / space.weightLimit : 0;

  /* ------------------------------------------------------------- creation */

  const createAt = (w: number, h: number, pos?: { x: number; y: number }) => {
    const id = actions.createObject({
      name: "New object",
      type: "furniture",
      dims: dimsForFootprint(space, w, h, step),
    });
    actions.placeObject(spaceId, id, pos);
    onSelectObject(id);
    drawer.open(id, { isNew: true });
  };

  const addObject = () => {
    createAt(Math.min(step * 4, dims.x), Math.min(step * 4, dims.y));
  };

  /* -------------------------------------------------------------- pointer */

  const cancelDrag = () => {
    const current = dragRef.current;
    if (!current) return;
    const svg = svgRef.current;
    if (svg?.hasPointerCapture(current.pointerId)) svg.releasePointerCapture(current.pointerId);
    setDrag(null);
  };

  const drawRect = (a: { x: number; y: number }, b: { x: number; y: number }): Rect => {
    const x0 = clamp(snap(Math.min(a.x, b.x), snapStep), 0, dims.x);
    const y0 = clamp(snap(Math.min(a.y, b.y), snapStep), 0, dims.y);
    const x1 = clamp(snap(Math.max(a.x, b.x), snapStep), 0, dims.x);
    const y1 = clamp(snap(Math.max(a.y, b.y), snapStep), 0, dims.y);
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  };

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0 || dragRef.current) return;
    const svg = e.currentTarget;
    const pt = toSvgPoint(svg, e.clientX, e.clientY);
    if (!pt) return;
    const target = e.target as Element;

    if (mode === "draw") {
      svg.setPointerCapture(e.pointerId);
      setDrag({
        kind: "draw",
        pointerId: e.pointerId,
        start: pt,
        rect: drawRect({ x: clamp(pt.x, 0, dims.x), y: clamp(pt.y, 0, dims.y) }, pt),
      });
      return;
    }

    const handleEl = target.closest<SVGElement>("[data-handle]");
    const rectEl = target.closest<SVGElement>("[data-rect-id]");
    const id = handleEl?.dataset.handle ?? rectEl?.dataset.rectId;
    const item = id ? items.find((i) => i.object.id === id) : undefined;
    if (!item) {
      onSelectObject(null);
      return;
    }
    onSelectObject(item.object.id);
    svg.setPointerCapture(e.pointerId);
    setDrag({
      kind: handleEl ? "resize" : "move",
      id: item.object.id,
      pointerId: e.pointerId,
      start: pt,
      origin: item.rect,
      rect: item.rect,
    });
  };

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const current = dragRef.current;
    if (!current || current.pointerId !== e.pointerId) return;
    const pt = toSvgPoint(e.currentTarget, e.clientX, e.clientY);
    if (!pt) return;
    if (current.kind === "draw") {
      const a = { x: clamp(current.start.x, 0, dims.x), y: clamp(current.start.y, 0, dims.y) };
      const b = { x: clamp(pt.x, 0, dims.x), y: clamp(pt.y, 0, dims.y) };
      setDrag({ ...current, rect: drawRect(a, b) });
      return;
    }
    const dx = pt.x - current.start.x;
    const dy = pt.y - current.start.y;
    const o = current.origin;
    if (current.kind === "move") {
      setDrag({
        ...current,
        rect: {
          ...o,
          x: clamp(snap(o.x + dx, snapStep), 0, dims.x - o.w),
          y: clamp(snap(o.y + dy, snapStep), 0, dims.y - o.h),
        },
      });
    } else {
      setDrag({
        ...current,
        rect: {
          ...o,
          w: clamp(snap(o.w + dx, snapStep), Math.min(snapStep, dims.x - o.x), dims.x - o.x),
          h: clamp(snap(o.h + dy, snapStep), Math.min(snapStep, dims.y - o.y), dims.y - o.y),
        },
      });
    }
  };

  const onPointerUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    const current = dragRef.current;
    if (!current || current.pointerId !== e.pointerId) return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setDrag(null);

    if (current.kind === "draw") {
      const r = current.rect;
      if (r.w >= snapStep && r.h >= snapStep) {
        createAt(r.w, r.h, { x: r.x, y: r.y });
        setMode("select");
      }
      return;
    }
    const { origin, rect } = current;
    if (rect.x !== origin.x || rect.y !== origin.y) actions.moveObject(spaceId, current.id, { x: rect.x, y: rect.y });
    if (rect.w !== origin.w || rect.h !== origin.h) actions.resizeObject(current.id, { w: rect.w, h: rect.h }, spaceId);
  };

  /* ------------------------------------------------------------- keyboard */

  const onRootKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable='true']")) return;
    if (e.key === "Escape") {
      cancelDrag();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "v" || e.key === "V") setMode("select");
    else if (e.key === "r" || e.key === "R") setMode("draw");
  };

  const onRectKeyDown = (e: ReactKeyboardEvent<SVGGElement>, item: (typeof items)[number]) => {
    if (e.target !== e.currentTarget) return;
    const { object, placement, rect } = item;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      actions.removeFromSpace(spaceId, object.id);
      if (selectedObjectId === object.id) onSelectObject(null);
      toast(`Removed ${object.name} from ${space.name}`, {
        actions: [{ label: "Undo", onClick: () => actions.restorePlacement(spaceId, placement) }],
      });
      rootRef.current?.focus();
      return;
    }
    const delta =
      e.key === "ArrowLeft" ? [-1, 0] : e.key === "ArrowRight" ? [1, 0] : e.key === "ArrowUp" ? [0, -1] : e.key === "ArrowDown" ? [0, 1] : null;
    if (!delta) return;
    e.preventDefault();
    const [dx, dy] = delta;
    if (e.shiftKey) {
      const w = clamp(round4(rect.w + dx * snapStep), Math.min(snapStep, dims.x - rect.x), dims.x - rect.x);
      const h = clamp(round4(rect.h + dy * snapStep), Math.min(snapStep, dims.y - rect.y), dims.y - rect.y);
      if (w !== rect.w || h !== rect.h) actions.resizeObject(object.id, { w, h }, spaceId);
    } else {
      const x = clamp(round4(rect.x + dx * snapStep), 0, dims.x - rect.w);
      const y = clamp(round4(rect.y + dy * snapStep), 0, dims.y - rect.h);
      if (x !== rect.x || y !== rect.y) actions.moveObject(spaceId, object.id, { x, y });
    }
  };

  /* --------------------------------------------------------------- render */

  const gridId = `${uid}-grid`;
  const drawing = drag?.kind === "draw" ? drag.rect : null;
  const drawingSizeText =
    drawing && (drawing.w > 0 || drawing.h > 0) ? formatDims({ x: drawing.w, y: drawing.h }, unit) : "";
  const sizeLabel = (() => {
    if (!drawing || !drawingSizeText) return null;
    const labelFontSize = fontSize * 0.85;
    const width = drawingSizeText.length * labelFontSize * 0.6 + labelFontSize * 1.2;
    const height = labelFontSize * 1.8;
    const gap = labelFontSize * 0.4;
    let y: number;
    if (drawing.y - gap - height >= -pad) y = drawing.y - gap - height;
    else if (drawing.y + drawing.h + gap + height <= dims.y + pad) y = drawing.y + drawing.h + gap;
    else y = drawing.y + gap;
    const minX = -pad + width / 2;
    const maxX = dims.x + pad - width / 2;
    const centerX = Math.min(Math.max(drawing.x + drawing.w / 2, minX), Math.max(minX, maxX));
    return { labelFontSize, width, height, x: centerX - width / 2, y, cx: centerX };
  })();
  const count = items.length;

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      onKeyDown={onRootKeyDown}
      className={cx("flex flex-col gap-3 outline-none", className)}
    >
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          aria-label="Designer tool"
          selectedKeys={[mode]}
          onSelectionChange={(keys) => {
            const next = [...keys][0];
            if (next === "select" || next === "draw") setMode(next);
          }}
        >
          <SegmentedControlItem id="select" aria-label="Select (V)">
            <span className="inline-flex items-center gap-1.5">
              <RiCursorLine aria-hidden className="size-4" />
              Select
            </span>
          </SegmentedControlItem>
          <SegmentedControlItem id="draw" aria-label="Draw (R)">
            <span className="inline-flex items-center gap-1.5">
              <RiShape2Line aria-hidden className="size-4" />
              Draw
            </span>
          </SegmentedControlItem>
        </SegmentedControl>
        <Button variant="secondary" size="small" leadingIcon={RiAddLine} onClick={addObject}>
          Add object
        </Button>
        <span className="ms-auto text-body-regular text-text-secondary tabular-nums" dir="ltr">
          Grid {formatLength(step, unit)} · Snap {formatLength(snapStep, unit)} · {formatDims(dims, unit)}
        </span>
      </div>

      <div
        dir="ltr"
        className="relative overflow-hidden rounded-3xl border border-border-button-default bg-background-secondary-default p-3"
      >
        <svg
          ref={svgRef}
          role="group"
          aria-label={`Designer for ${space.name}`}
          viewBox={`${-pad} ${-pad} ${dims.x + pad * 2} ${dims.y + pad * 2}`}
          preserveAspectRatio="xMidYMid meet"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={cancelDrag}
          className={cx("block max-h-[70vh] w-full touch-none select-none", mode === "draw" ? "cursor-crosshair" : "cursor-default")}
        >
          <defs>
            <pattern id={gridId} width={step} height={step} patternUnits="userSpaceOnUse">
              <path
                d={`M ${step} 0 H 0 V ${step}`}
                fill="none"
                stroke="var(--color-separator-border)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
            </pattern>
            {items.map(({ object, rect }) => (
              <clipPath key={object.id} id={`${uid}-clip-${object.id}`}>
                <rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} />
              </clipPath>
            ))}
          </defs>

          <rect x={0} y={0} width={dims.x} height={dims.y} fill="var(--color-background-primary-default)" />
          <rect x={0} y={0} width={dims.x} height={dims.y} fill={`url(#${gridId})`} />
          <rect
            x={0}
            y={0}
            width={dims.x}
            height={dims.y}
            fill="none"
            stroke="var(--color-border-button-default)"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />

          {items.map((item) => {
            const { object, rect } = item;
            const visual = OBJECT_TYPE_SVG[object.type];
            const selected = object.id === selectedObjectId;
            const overlaps = overlapping.has(object.id);
            const sizeText = formatDims({ x: rect.w, y: rect.h }, unit);
            const showLabel = rect.w >= fontSize * 4 && rect.h >= fontSize * 2.6;
            const label = `${object.name}, ${sizeText}, at ${toDisplayLength(rect.x, unit)}, ${toDisplayLength(rect.y, unit)}${overlaps ? ", overlaps another object" : ""}`;
            const stroke = selected
              ? "var(--color-accent-500)"
              : overlaps
                ? "var(--color-border-error-default)"
                : visual.stroke;
            return (
              <g
                key={object.id}
                data-rect-id={object.id}
                tabIndex={0}
                role="button"
                aria-label={label}
                aria-pressed={selected}
                onFocus={() => {
                  if (!selected) onSelectObject(object.id);
                }}
                onKeyDown={(e) => onRectKeyDown(e, item)}
                className={cx("group outline-none", mode === "draw" ? "cursor-crosshair" : "cursor-move")}
              >
                {overlaps ? <title>{`${object.name} overlaps another object`}</title> : null}
                <rect
                  x={rect.x}
                  y={rect.y}
                  width={rect.w}
                  height={rect.h}
                  fill={visual.fill}
                  stroke={stroke}
                  strokeWidth={selected ? 3 : 1.5}
                  strokeDasharray={overlaps && !selected ? "6 4" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
                {showLabel ? (
                  <g clipPath={`url(#${uid}-clip-${object.id})`} className="pointer-events-none">
                    <text
                      x={rect.x + fontSize * 0.5}
                      y={rect.y + fontSize * 1.4}
                      fontSize={fontSize}
                      fontWeight={600}
                      style={{ fill: "var(--color-text-primary)" }}
                    >
                      {object.name}
                    </text>
                    <text
                      x={rect.x + fontSize * 0.5}
                      y={rect.y + fontSize * 2.6}
                      fontSize={fontSize * 0.85}
                      style={{ fill: "var(--color-text-secondary)" }}
                    >
                      {sizeText}
                    </text>
                  </g>
                ) : null}
                <rect
                  x={rect.x - fontSize * 0.25}
                  y={rect.y - fontSize * 0.25}
                  width={rect.w + fontSize * 0.5}
                  height={rect.h + fontSize * 0.5}
                  fill="none"
                  stroke="var(--color-accent-500)"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                  className="pointer-events-none opacity-0 group-focus-visible:opacity-100"
                />
              </g>
            );
          })}

          {mode === "select"
            ? items
                .filter((i) => i.object.id === selectedObjectId)
                .map(({ object, rect }) => (
                  <rect
                    key={object.id}
                    data-handle={object.id}
                    x={rect.x + rect.w - handleSize / 2}
                    y={rect.y + rect.h - handleSize / 2}
                    width={handleSize}
                    height={handleSize}
                    rx={handleSize * 0.15}
                    fill="var(--color-accent-500)"
                    stroke="var(--color-background-primary-default)"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                    className="cursor-nwse-resize"
                  />
                ))
            : null}

          {drawing ? (
            <rect
              x={drawing.x}
              y={drawing.y}
              width={drawing.w}
              height={drawing.h}
              fill="color-mix(in oklab, var(--color-accent-500) 12%, transparent)"
              stroke="var(--color-accent-500)"
              strokeWidth={2}
              strokeDasharray="6 4"
              vectorEffect="non-scaling-stroke"
              className="pointer-events-none"
            />
          ) : null}

          {sizeLabel ? (
            <g className="pointer-events-none" aria-hidden="true">
              <rect
                x={sizeLabel.x}
                y={sizeLabel.y}
                width={sizeLabel.width}
                height={sizeLabel.height}
                rx={sizeLabel.height / 2}
                fill="var(--color-accent-600)"
              />
              <text
                x={sizeLabel.cx}
                y={sizeLabel.y + sizeLabel.height / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={sizeLabel.labelFontSize}
                fontWeight={600}
                fill="var(--color-text-white)"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {drawingSizeText}
              </text>
            </g>
          ) : null}
        </svg>

        <span className="sr-only" aria-live="polite">
          {drawingSizeText}
        </span>

        {count === 0 && !drawing ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <p className="rounded-full bg-background-primary-default px-4 py-2 text-body-medium text-text-secondary shadow-xs">
              Draw a rectangle or press Add object
            </p>
          </div>
        ) : null}
      </div>

      <div className="flex flex-col gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 text-body-regular text-text-secondary">
          <span>
            <span className="text-body-medium text-text-primary">{count}</span> {count === 1 ? "object" : "objects"}
          </span>
          <span className="tabular-nums">
            Est. total <span className="text-body-medium text-text-primary">{formatPrice(total)}</span>
          </span>
        </div>
        {space.weightLimit ? (
          <Meter
            label="Weight"
            value={weight}
            minValue={0}
            maxValue={space.weightLimit}
            valueLabel={`${Math.round(weight * 10) / 10} / ${space.weightLimit} lb`}
            tone={weightRatio > 1 ? "error" : weightRatio > 0.8 ? "warning" : "accent"}
          />
        ) : null}
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-caption-1-medium text-text-tertiary">
          <span>Drag to move</span>·<span>corner to resize</span>·
          <span className="inline-flex items-center gap-1">
            <Kbd>R</Kbd> to draw
          </span>
          ·
          <span className="inline-flex items-center gap-1">
            <Kbd>Del</Kbd> to remove
          </span>
        </p>
      </div>
    </div>
  );
}
