import type { ComponentType } from "react";
import {
  RiArchiveDrawerLine,
  RiImageLine,
  RiLayoutGridLine,
  RiLightbulbLine,
  RiPlantLine,
  RiSofaLine,
} from "@remixicon/react";
import type { ObjectType } from "@/mocks/types";

type IconComponent = ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;

export interface ObjectTypeVisual {
  icon: IconComponent;
  /** Soft tile/rectangle fill. */
  surface: string;
  /** Stroke for designer rectangles and tile outlines. */
  stroke: string;
  /** Icon color drawn on `surface`. */
  iconColor: string;
}

/**
 * One visual per object type, shared by product tiles, object cards and the
 * space designer. Chart tokens only (no lime: that hue is reserved for the
 * green "selected" state). Class strings are literal so Tailwind sees them.
 */
export const OBJECT_TYPE_VISUALS: Record<ObjectType, ObjectTypeVisual> = {
  furniture: { icon: RiSofaLine, surface: "bg-chart-1/20", stroke: "border-chart-1-active", iconColor: "text-chart-1-active" },
  lighting: { icon: RiLightbulbLine, surface: "bg-chart-4/20", stroke: "border-chart-4-active", iconColor: "text-chart-4-active" },
  rug: { icon: RiLayoutGridLine, surface: "bg-chart-5/20", stroke: "border-chart-5-active", iconColor: "text-chart-5-active" },
  "wall-art": { icon: RiImageLine, surface: "bg-chart-3/20", stroke: "border-chart-3-active", iconColor: "text-chart-3-active" },
  storage: { icon: RiArchiveDrawerLine, surface: "bg-chart-neutral/30", stroke: "border-chart-neutral", iconColor: "text-foreground-icon-secondary" },
  decor: { icon: RiPlantLine, surface: "bg-chart-4/20", stroke: "border-chart-4-active", iconColor: "text-chart-4-active" },
};

/** SVG-friendly fill/stroke tokens for the designer (CSS variables, theme-aware). */
export const OBJECT_TYPE_SVG: Record<ObjectType, { fill: string; stroke: string }> = {
  furniture: { fill: "color-mix(in oklab, var(--color-chart-1) 28%, transparent)", stroke: "var(--color-chart-1-active)" },
  lighting: { fill: "color-mix(in oklab, var(--color-chart-4) 28%, transparent)", stroke: "var(--color-chart-4-active)" },
  rug: { fill: "color-mix(in oklab, var(--color-chart-5) 22%, transparent)", stroke: "var(--color-chart-5-active)" },
  "wall-art": { fill: "color-mix(in oklab, var(--color-chart-3) 28%, transparent)", stroke: "var(--color-chart-3-active)" },
  storage: { fill: "color-mix(in oklab, var(--color-chart-neutral) 40%, transparent)", stroke: "var(--color-chart-neutral)" },
  decor: { fill: "color-mix(in oklab, var(--color-chart-4) 28%, transparent)", stroke: "var(--color-chart-4-active)" },
};
