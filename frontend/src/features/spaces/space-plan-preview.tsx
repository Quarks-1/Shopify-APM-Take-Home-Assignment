import { useAppState } from "@/state/app-store";
import { footprint, getObject, getSpace } from "@/state/selectors";
import { OBJECT_TYPE_SVG } from "@/features/objects/object-type-visuals";
import { cx } from "@/utils/cx";

export interface SpacePlanPreviewProps {
  spaceId: string;
  className?: string;
}

/** Static, non-interactive SVG thumbnail of a space. The viewBox is in inches. */
export function SpacePlanPreview({ spaceId, className }: SpacePlanPreviewProps) {
  const state = useAppState();
  const space = getSpace(state, spaceId);
  if (!space) return null;

  const { x: width, y: height } = space.dims;
  const pad = Math.max(width, height) * 0.02;
  const rects = space.placements.flatMap((p) => {
    const object = getObject(state, p.objectId);
    if (!object) return [];
    const { w, h } = footprint(object, space);
    return [{ id: object.id, x: p.x, y: p.y, w, h, visual: OBJECT_TYPE_SVG[object.type] }];
  });
  const label = `Plan of ${space.name}, ${rects.length} ${rects.length === 1 ? "object" : "objects"}`;

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`${-pad} ${-pad} ${width + pad * 2} ${height + pad * 2}`}
      preserveAspectRatio="xMidYMid meet"
      className={cx("pointer-events-none block size-full", className)}
    >
      <title>{label}</title>
      <rect
        x={0}
        y={0}
        width={width}
        height={height}
        fill="var(--color-background-primary-default)"
        stroke="var(--color-border-button-default)"
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
      />
      {rects.map((r) => (
        <rect
          key={r.id}
          x={r.x}
          y={r.y}
          width={r.w}
          height={r.h}
          fill={r.visual.fill}
          stroke={r.visual.stroke}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}
