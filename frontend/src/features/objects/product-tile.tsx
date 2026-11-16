import type { ObjectType } from "@/mocks/types";
import { cx } from "@/utils/cx";
import { OBJECT_TYPE_VISUALS } from "./object-type-visuals";

export type ProductTileSize = "sm" | "md" | "lg";

export interface ProductTileProps {
  type: ObjectType;
  /** `sm` is a fixed 40px square; `md` and `lg` fill the width of their parent. */
  size?: ProductTileSize;
  className?: string;
}

const SIZE_CLASSES: Record<ProductTileSize, { box: string; icon: string }> = {
  sm: { box: "size-10 shrink-0", icon: "size-5" },
  md: { box: "aspect-square w-full", icon: "size-10" },
  lg: { box: "aspect-square w-full", icon: "size-14" },
};

/** Placeholder product image: a tinted square with the object type's icon. */
export function ProductTile({ type, size = "md", className }: ProductTileProps) {
  const visual = OBJECT_TYPE_VISUALS[type];
  const Icon = visual.icon;
  const sizing = SIZE_CLASSES[size];
  return (
    <div
      aria-hidden
      className={cx("flex items-center justify-center rounded-2xl", visual.surface, sizing.box, className)}
    >
      <Icon aria-hidden className={cx(sizing.icon, visual.iconColor)} />
    </div>
  );
}
