import type { ReactNode } from "react";
import { RiCheckLine, RiExternalLinkLine, RiStarFill } from "@remixicon/react";
import { Chip } from "@/components/base/badges/chip";
import { IconLinkButton } from "@/components/base/buttons/icon-button";
import type { Product, ProductRating } from "@/mocks/types";
import { formatDims, formatPrice } from "@/lib/format";
import { useAppState } from "@/state/app-store";
import { getStore } from "@/state/selectors";
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

/** Catalog sizes can be partial or missing, and are not always confirmed to fit. */
export function ProductSize({ product, className }: { product: Product; className?: string }) {
  const text = formatDims(product.dims, "in");
  const unverified = product.sizeStatus === "unverified";
  if (!text) {
    return <span className={cx("text-caption-1-medium text-text-placeholder", className)}>Size unverified</span>;
  }
  return (
    <span
      className={cx("text-caption-1-medium text-text-tertiary", className)}
      title={unverified ? "The listing doesn't confirm this size fits" : undefined}
    >
      {text}
      {unverified ? <span className="text-text-placeholder"> · unverified</span> : null}
    </span>
  );
}

/** Star rating with one decimal, e.g. "★ 4.8". */
export function ProductRatingText({ rating, className }: { rating: ProductRating; className?: string }) {
  const value = rating.value.toFixed(1);
  return (
    <span className={cx("inline-flex items-center gap-1", className)}>
      <RiStarFill aria-hidden className="size-3.5 shrink-0 text-chart-8" />
      <span aria-hidden>{value}</span>
      <span className="sr-only">Rated {value} out of 5</span>
    </span>
  );
}

function ProductDetails({ product, variant }: { product: Product; variant: ProductCardVariant }) {
  const store = getStore(useAppState(), product.storeId);
  // Candidate cards are buttons, so a link can't sit inside them; the selected card carries it.
  const link = variant !== "candidate" && product.url ? product.url : undefined;
  return (
    <>
      <span className="relative block">
        <ProductTile type={product.type} imageUrl={product.imageUrl} size="md" />
        {link ? (
          <IconLinkButton
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            icon={RiExternalLinkLine}
            size="small"
            aria-label={`View ${product.title}${store ? ` on ${store.name}` : ""} (opens in a new tab)`}
            title="View product page"
            className="absolute end-2 top-2"
          />
        ) : null}
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
          <span className="ms-auto flex flex-col items-end gap-0.5">
            {product.rating ? (
              <ProductRatingText rating={product.rating} className="text-caption-1-medium text-text-secondary" />
            ) : null}
            <ProductSize product={product} />
          </span>
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
