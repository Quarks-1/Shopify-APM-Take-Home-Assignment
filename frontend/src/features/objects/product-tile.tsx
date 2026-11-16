import { useState } from "react";
import type { ObjectType } from "@/mocks/types";
import { cx } from "@/utils/cx";
import { OBJECT_TYPE_VISUALS } from "./object-type-visuals";

export type ProductTileSize = "sm" | "md" | "lg";

export interface ProductTileProps {
  type: ObjectType;
  /** Real product photo; the type icon tile shows when missing or when it fails to load. */
  imageUrl?: string;
  /** `sm` is a fixed 40px square; `md` and `lg` fill the width of their parent. */
  size?: ProductTileSize;
  className?: string;
}

const SIZE_CLASSES: Record<ProductTileSize, { box: string; icon: string }> = {
  sm: { box: "size-10 shrink-0", icon: "size-5" },
  md: { box: "aspect-square w-full", icon: "size-10" },
  lg: { box: "aspect-square w-full", icon: "size-14" },
};

/** Product image square: the photo when there is one, else a tinted square with the object type's icon. */
export function ProductTile({ type, imageUrl, size = "md", className }: ProductTileProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const sizing = SIZE_CLASSES[size];

  if (imageUrl && imageUrl !== failedUrl) {
    return (
      <img
        src={imageUrl}
        // Decorative: the product title is always shown next to it.
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setFailedUrl(imageUrl)}
        className={cx("block rounded-2xl bg-background-secondary-default object-cover", sizing.box, className)}
      />
    );
  }

  const visual = OBJECT_TYPE_VISUALS[type];
  const Icon = visual.icon;
  return (
    <div
      aria-hidden
      className={cx("flex items-center justify-center rounded-2xl", visual.surface, sizing.box, className)}
    >
      <Icon aria-hidden className={cx(sizing.icon, visual.iconColor)} />
    </div>
  );
}
