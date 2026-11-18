import type { ReactNode } from "react";
import {
  RiCheckLine,
  RiCloseLine,
  RiExternalLinkLine,
  RiStarFill,
  RiThumbDownFill,
  RiThumbDownLine,
  RiThumbUpFill,
  RiThumbUpLine,
} from "@remixicon/react";
import { Chip } from "@/components/base/badges/chip";
import { IconLinkButton } from "@/components/base/buttons/icon-button";
import type { LengthUnit, Product, ProductRating, ProductVote } from "@/mocks/types";
import { formatDims, formatPrice, toDisplayLength } from "@/lib/format";
import { useAppState } from "@/state/app-store";
import { getStore } from "@/state/selectors";
import { cx } from "@/utils/cx";
import { ProductTile } from "./product-tile";

export type ProductCardVariant = "candidate" | "selected" | "cart";

export interface ProductCardProps {
  product: Product;
  variant: ProductCardVariant;
  /** Display length unit for product dimensions (default `"in"`). */
  unit?: LengthUnit;
  /** Called when a `candidate` or `selected` card is pressed (to select or deselect). */
  onSelect?: () => void;
  /** Rendered below the details of a `cart` card (quantity, remove, ...). */
  footer?: ReactNode;
  /** The price is within the fuzzy margin past an approximate budget end. */
  priceNote?: "over" | "under";
  /** The user's thumbs vote on this product. */
  vote?: ProductVote;
  /** Shows the thumbs up/down buttons on `candidate` and `selected` cards; `null` clears the vote. */
  onVote?: (vote: ProductVote | null) => void;
  className?: string;
}

const SURFACE = "rounded-3xl border border-border-button-default bg-background-primary-default p-3";

/** Catalog sizes can be partial or missing, and are not always confirmed to fit. */
export function ProductSize({
  product,
  unit = "in",
  className,
}: {
  product: Product;
  unit?: LengthUnit;
  className?: string;
}) {
  const dimsText = formatDims(product.dims, unit);
  const longestText =
    !dimsText && typeof product.dimsLongest === "number" && product.dimsLongest > 0
      ? `${toDisplayLength(product.dimsLongest, unit)} ${unit} max`
      : "";
  const text = dimsText || longestText;
  const unverified = product.sizeStatus === "unverified";
  if (!text) {
    return <span className={cx("text-caption-1-medium text-text-placeholder", className)}>Size unverified</span>;
  }
  return (
    <span
      className={cx("text-caption-1-medium text-text-tertiary", className)}
      title={unverified ? "The listing doesn't confirm this size fits" : undefined}
    >
      <bdi>{text}</bdi>
      {unverified ? <span className="text-text-placeholder"> · unverified</span> : null}
    </span>
  );
}

/** Star rating with one decimal, e.g. "★ 4.8". */
export function ProductRatingText({ rating, className }: { rating: ProductRating; className?: string }) {
  const value = rating.value.toFixed(1);
  return (
    <span className={cx("inline-flex items-center gap-1", className)}>
      <RiStarFill aria-hidden className="size-3.5 shrink-0 text-status-yellow-text" />
      <span aria-hidden>{value}</span>
      <span className="sr-only">Rated {value} out of 5</span>
    </span>
  );
}

function VoteButtons({
  title,
  vote,
  onVote,
}: {
  title: string;
  vote?: ProductVote;
  onVote: (vote: ProductVote | null) => void;
}) {
  const base = cx(
    "inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-lg",
    "transition-colors duration-150 hover:bg-background-secondary-hover",
    "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
  );
  const UpIcon = vote === "up" ? RiThumbUpFill : RiThumbUpLine;
  const DownIcon = vote === "down" ? RiThumbDownFill : RiThumbDownLine;
  return (
    <span className="relative z-20 flex shrink-0 items-center">
      <button
        type="button"
        aria-pressed={vote === "up"}
        aria-label={`Like ${title}`}
        onClick={() => onVote(vote === "up" ? null : "up")}
        className={cx(
          base,
          vote === "up"
            ? "text-notification-success-foreground"
            : "text-foreground-icon-tertiary hover:text-notification-success-foreground",
        )}
      >
        <UpIcon aria-hidden className="size-4" />
      </button>
      <button
        type="button"
        aria-pressed={vote === "down"}
        aria-label={`Dislike ${title}`}
        onClick={() => onVote(vote === "down" ? null : "down")}
        className={cx(
          base,
          vote === "down"
            ? "text-text-error-primary"
            : "text-foreground-icon-tertiary hover:text-text-error-primary",
        )}
      >
        <DownIcon aria-hidden className="size-4" />
      </button>
    </span>
  );
}

function ProductDetails({
  product,
  variant,
  unit,
  canToggle,
  priceNote,
  vote,
  onVote,
}: {
  product: Product;
  variant: ProductCardVariant;
  unit?: LengthUnit;
  canToggle?: boolean;
  priceNote?: "over" | "under";
  vote?: ProductVote;
  onVote?: (vote: ProductVote | null) => void;
}) {
  const dim = vote === "down" ? "opacity-50" : undefined;
  const store = getStore(useAppState(), product.storeId);
  const link = product.url || undefined;
  return (
    <>
      <span className="relative block">
        <span className={cx("block", dim)}>
          <ProductTile type={product.type} imageUrl={product.imageUrl} size="md" />
        </span>
        {link ? (
          <IconLinkButton
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            icon={RiExternalLinkLine}
            size="small"
            aria-label={`View ${product.title}${store ? ` on ${store.name}` : ""} (opens in a new tab)`}
            title="View product page"
            className="absolute end-2 top-2 z-20"
          />
        ) : null}
        {variant === "candidate" ? (
          <span
            aria-hidden
            className={cx(
              "pointer-events-none absolute start-2 top-2 rounded-full bg-background-primary-default px-2 py-0.5 text-caption-1-semibold text-text-primary shadow-xs",
              "opacity-0 transition-opacity duration-150 group-has-[[data-card-action]:hover]/product:opacity-100 group-has-[[data-card-action]:focus-visible]/product:opacity-100",
            )}
          >
            Select
          </span>
        ) : null}
        {variant === "selected" ? (
          <>
            <Chip
              color="lime"
              variant="caption"
              className={cx(
                "pointer-events-none absolute start-2 top-2 gap-1 transition-opacity duration-150",
                canToggle &&
                  "group-has-[[data-card-action]:hover]/product:opacity-0 group-has-[[data-card-action]:focus-visible]/product:opacity-0",
              )}
            >
              <RiCheckLine aria-hidden className="size-3.5" />
              Selected
            </Chip>
            {canToggle ? (
              <Chip
                aria-hidden
                color="neutral"
                variant="caption"
                className={cx(
                  "pointer-events-none absolute start-2 top-2 gap-1 opacity-0 transition-opacity duration-150",
                  "group-has-[[data-card-action]:hover]/product:opacity-100 group-has-[[data-card-action]:focus-visible]/product:opacity-100",
                )}
              >
                <RiCloseLine aria-hidden className="size-3.5" />
                Deselect
              </Chip>
            ) : null}
          </>
        ) : null}
        {priceNote ? (
          <span className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
            <Chip color="yellow" variant="caption">
              {priceNote === "over" ? "Slightly over budget" : "Slightly under minimum"}
            </Chip>
          </span>
        ) : null}
      </span>
      <span className="flex min-w-0 flex-col gap-1 text-start">
        <span className={cx("truncate text-body-medium text-text-primary", dim)}>{product.title}</span>
        <span className="flex min-h-5 items-center justify-between gap-2">
          <span className={cx("min-w-0 flex-1 truncate text-body-2-regular text-text-secondary", dim)}>{store?.name}</span>
          {product.rating ? (
            <ProductRatingText
              rating={product.rating}
              className={cx("shrink-0 text-caption-1-medium text-text-secondary", dim)}
            />
          ) : null}
          {onVote && variant !== "cart" ? <VoteButtons title={product.title} vote={vote} onVote={onVote} /> : null}
        </span>
        <span className={cx("flex items-center justify-between gap-2", dim)}>
          <bdi className="shrink-0 text-headline-medium text-text-primary">{formatPrice(product.price)}</bdi>
          <ProductSize product={product} unit={unit} className="min-w-0 text-end" />
        </span>
      </span>
    </>
  );
}

export function ProductCard({
  product,
  variant,
  unit,
  onSelect,
  footer,
  priceNote,
  vote,
  onVote,
  className,
}: ProductCardProps) {
  if (variant === "candidate") {
    return (
      <div
        className={cx(
          "group/product relative flex w-full flex-col gap-3",
          SURFACE,
          "transition-colors duration-150 hover:border-border-button-hover hover:bg-background-secondary-hover",
          className,
        )}
      >
        <button
          type="button"
          data-card-action=""
          onClick={onSelect}
          aria-label={`Select ${product.title}`}
          className="absolute inset-0 z-10 cursor-pointer rounded-3xl outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
        />
        <ProductDetails
          product={product}
          variant={variant}
          unit={unit}
          priceNote={priceNote}
          vote={vote}
          onVote={onVote}
        />
      </div>
    );
  }

  const canDeselect = variant === "selected" && onSelect !== undefined;

  return (
    <div
      className={cx(
        "group/product relative flex w-full flex-col gap-3",
        SURFACE,
        variant === "selected" && "bg-notification-success-background ring-2 ring-notification-success-foreground",
        canDeselect && "transition-colors duration-150",
        className,
      )}
    >
      {canDeselect ? (
        <button
          type="button"
          data-card-action=""
          aria-pressed={true}
          onClick={onSelect}
          aria-label={`Deselect ${product.title}`}
          className="absolute inset-0 z-10 cursor-pointer rounded-3xl outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
        />
      ) : null}
      <ProductDetails
        product={product}
        variant={variant}
        unit={unit}
        canToggle={canDeselect}
        priceNote={priceNote}
        vote={vote}
        onVote={onVote}
      />
      {variant === "cart" && footer ? <div>{footer}</div> : null}
    </div>
  );
}
