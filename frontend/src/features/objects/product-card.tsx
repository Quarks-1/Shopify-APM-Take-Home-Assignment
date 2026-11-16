import type { ReactNode } from "react";
import { RiCheckLine } from "@remixicon/react";
import { Chip } from "@/components/base/badges/chip";
import { getStoreById } from "@/mocks/catalog";
import type { Product } from "@/mocks/types";
import { formatDims, formatPrice } from "@/lib/format";
import { cx } from "@/utils/cx";
import { ProductTile } from "./product-tile";

export type ProductCardVariant = "candidate" | "selected" | "cart";

export interface ProductCardProps {
  product: Product;
  variant: ProductCardVariant;
  /** Called when a `candidate` card is pressed. */
  onSelect?: () => void;
  /** Rendered below the details of a `cart` card (quantity, remove, ...). */
  footer?: ReactNode;
  className?: string;
}

const SURFACE = "rounded-3xl border border-border-button-default bg-background-primary-default p-3";

function ProductDetails({ product, variant }: { product: Product; variant: ProductCardVariant }) {
  const store = getStoreById(product.storeId);
  return (
    <>
      <span className="relative block">
        <ProductTile type={product.type} size="md" />
        {variant === "candidate" ? (
          <span
            aria-hidden
            className={cx(
              "absolute end-2 top-2 rounded-full bg-background-primary-default px-2 py-0.5 text-caption-1-semibold text-text-primary shadow-xs",
              "opacity-0 transition-opacity duration-150 group-hover/product:opacity-100 group-focus-visible/product:opacity-100",
            )}
          >
            Select
          </span>
        ) : null}
        {variant === "selected" ? (
          <Chip color="lime" variant="caption" className="absolute start-2 top-2 gap-1">
            <RiCheckLine aria-hidden className="size-3.5" />
            Selected
          </Chip>
        ) : null}
      </span>
      <span className="flex min-w-0 flex-col gap-1 text-start">
        <span className="line-clamp-2 text-body-medium text-text-primary">{product.title}</span>
        {store ? <span className="truncate text-body-2-regular text-text-secondary">{store.name}</span> : null}
        <span className="flex flex-wrap items-baseline justify-between gap-x-2">
          <bdi className="text-headline-medium text-text-primary">{formatPrice(product.price)}</bdi>
          <span className="text-caption-1-medium text-text-tertiary">{formatDims(product.dims, "in")}</span>
        </span>
      </span>
    </>
  );
}

export function ProductCard({ product, variant, onSelect, footer, className }: ProductCardProps) {
  if (variant === "candidate") {
    return (
      <button
        type="button"
        onClick={onSelect}
        aria-label={`Select ${product.title}`}
        className={cx(
          "group/product flex w-full cursor-pointer flex-col gap-3 text-start outline-none",
          SURFACE,
          "transition-colors duration-150 hover:border-border-button-hover hover:bg-background-secondary-hover",
          "focus-visible:ring-2 focus-visible:ring-border-focus-ring",
          className,
        )}
      >
        <ProductDetails product={product} variant={variant} />
      </button>
    );
  }

  return (
    <div
      className={cx(
        "flex w-full flex-col gap-3",
        SURFACE,
        variant === "selected" && "bg-notification-success-background ring-2 ring-notification-success-foreground",
        className,
      )}
    >
      <ProductDetails product={product} variant={variant} />
      {variant === "cart" && footer ? <div>{footer}</div> : null}
    </div>
  );
}
