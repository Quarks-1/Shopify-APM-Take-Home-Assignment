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
  /** The price is within the fuzzy margin past an approximate budget end. */
  priceNote?: "over" | "under";
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

function ProductDetails({
  product,
  variant,
  priceNote,
}: {
  product: Product;
  variant: ProductCardVariant;
  priceNote?: "over" | "under";
}) {
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
        <span className="truncate text-body-medium text-text-primary">{product.title}</span>
        <span className="flex min-h-5 items-center justify-between gap-2">
          <span className="min-w-0 flex-1 truncate text-body-2-regular text-text-secondary">{store?.name}</span>
          {product.rating ? (
            <ProductRatingText rating={product.rating} className="shrink-0 text-caption-1-medium text-text-secondary" />
          ) : null}
        </span>
        {priceNote ? (
          <Chip color="yellow" variant="caption" className="self-start">
            {priceNote === "over" ? "Slightly over budget" : "Slightly under minimum"}
          </Chip>
        ) : variant !== "cart" ? (
          <Chip color="yellow" variant="caption" aria-hidden className="invisible self-start">
            Slightly over budget
          </Chip>
        ) : null}
        <span className="flex items-center justify-between gap-2">
          <bdi className="shrink-0 text-headline-medium text-text-primary">{formatPrice(product.price)}</bdi>
          <ProductSize product={product} className="min-w-0 text-end" />
        </span>
      </span>
    </>
  );
}

export function ProductCard({ product, variant, onSelect, footer, priceNote, className }: ProductCardProps) {
  if (variant === "candidate") {
    return (
      <button
        type="button"
        onClick={onSelect}
        aria-label={`Select ${product.title}`}
        className={cx(
          "group/product flex h-full w-full cursor-pointer flex-col gap-3 text-start outline-none",
          SURFACE,
          "transition-colors duration-150 hover:border-border-button-hover hover:bg-background-secondary-hover",
          "focus-visible:ring-2 focus-visible:ring-border-focus-ring",
          className,
        )}
      >
        <ProductDetails product={product} variant={variant} priceNote={priceNote} />
      </button>
    );
  }

  return (
    <div
      className={cx(
        "flex h-full w-full flex-col gap-3",
        SURFACE,
        variant === "selected" && "bg-notification-success-background ring-2 ring-notification-success-foreground",
        className,
      )}
    >
      <ProductDetails product={product} variant={variant} priceNote={priceNote} />
      {variant === "cart" && footer ? <div>{footer}</div> : null}
    </div>
  );
}
