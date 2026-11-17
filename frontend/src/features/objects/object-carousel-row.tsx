import { RiCheckLine, RiRefreshLine, RiSearchLine, RiSparkling2Line, RiShoppingCart2Line } from "@remixicon/react";
import { useNavigate } from "react-router";
import { priceBand } from "@/lib/price-scale";
import { Chip } from "@/components/base/badges/chip";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { toast } from "@/components/base/toast/toast";
import { formatDims, formatPrice, formatRelativeTime } from "@/lib/format";
import { OBJECT_TYPE_OPTIONS } from "@/mocks/types";
import { candidateIdOf } from "@/state/remote";
import { useAppActions, useAppState } from "@/state/app-store";
import {
  getCandidateProducts,
  getCartLineForObject,
  getObject,
  getProduct,
  getSelectedProduct,
  getSpace,
} from "@/state/selectors";
import { cx } from "@/utils/cx";
import { ProductCard } from "./product-card";
import { ProductTile } from "./product-tile";
import { SearchProgress } from "./search-progress";
import { useObjectDrawer } from "./use-object-drawer";
import { useObjectSearch } from "./use-object-search";

export interface ObjectCarouselRowProps {
  objectId: string;
  /** When set, dimensions are shown in this space's unit. */
  spaceId?: string;
  highlighted?: boolean;
  onFocusRow?: (objectId: string) => void;
}

const CARD_WIDTH = "w-56";

function SkeletonCard() {
  return (
    <div className={cx("flex shrink-0 flex-col gap-3 rounded-3xl border border-border-button-default p-3", CARD_WIDTH)}>
      <Skeleton className="aspect-square w-full" />
      <Skeleton shape="text" className="h-4 w-4/5" />
      <Skeleton shape="text" className="h-3.5 w-1/2" />
    </div>
  );
}

export function ObjectCarouselRow({ objectId, spaceId, highlighted = false, onFocusRow }: ObjectCarouselRowProps) {
  const state = useAppState();
  const actions = useAppActions();
  const drawer = useObjectDrawer();
  const search = useObjectSearch();
  const navigate = useNavigate();

  const object = getObject(state, objectId);
  if (!object) return null;

  const unit = (spaceId ? getSpace(state, spaceId)?.unit : undefined) ?? "in";
  const typeLabel = OBJECT_TYPE_OPTIONS.find((o) => o.value === object.type)?.label ?? object.type;
  const owned = object.source === "imported";
  const minFuzzy = object.priceMin !== undefined && !!object.priceMinFuzzy;
  const maxFuzzy = object.priceLimit !== undefined && !!object.priceLimitFuzzy;
  const minText = object.priceMin !== undefined ? `${minFuzzy ? "~" : ""}${formatPrice(object.priceMin)}` : "";
  const maxText = object.priceLimit !== undefined ? `${maxFuzzy ? "~" : ""}${formatPrice(object.priceLimit)}` : "";
  const priceRangeLabel =
    object.priceMin !== undefined && object.priceLimit !== undefined
      ? `${minText} – ${maxText}`
      : object.priceLimit !== undefined
        ? `≤ ${maxText}`
        : object.priceMin !== undefined
          ? `≥ ${minText}`
          : undefined;
  const fuzzyTitle = [
    minFuzzy && object.priceMin !== undefined ? `around ${formatPrice(object.priceMin)} (±15%)` : null,
    maxFuzzy && object.priceLimit !== undefined ? `around ${formatPrice(object.priceLimit)} (±15%)` : null,
  ]
    .filter(Boolean)
    .join(", ");
  const priceNoteFor = (price: number): "over" | "under" | undefined => {
    const band = priceBand(price, {
      min: object.priceMin,
      max: object.priceLimit,
      minFuzzy: object.priceMinFuzzy,
      maxFuzzy: object.priceLimitFuzzy,
    });
    return band === "over" || band === "under" ? band : undefined;
  };
  const running = search.isRunning(objectId) || object.search.status === "running";
  const cartLine = getCartLineForObject(state, objectId);
  const selectedProduct = getSelectedProduct(state, object);
  const ownedProduct = object.owned ? getProduct(state, object.owned.productId) : undefined;

  const candidates = getCandidateProducts(state, object).sort(
    (a, b) => Number(b.id === object.selectedProductId) - Number(a.id === object.selectedProductId),
  );

  const hasSearched = object.search.status === "done" || object.search.status === "empty" || candidates.length > 0;
  const voteValues = Object.values(object.votes ?? {});
  const likedCount = voteValues.filter((v) => v === "up").length;
  const dislikedCount = voteValues.length - likedCount;
  const refineHint = voteValues.length > 0 ? `Using ${likedCount} liked, ${dislikedCount} disliked` : "Re-run using your thumbs votes";
  const learned = search.preferences(objectId);
  const learnedText = learned
    ? [
        learned.likes.length > 0 ? `likes ${learned.likes.join(", ")}` : null,
        learned.avoids.length > 0 ? `avoids ${learned.avoids.join(", ")}` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  const voteOf = (product: { productGid?: string }) => (product.productGid ? object.votes?.[product.productGid] : undefined);

  const likeCandidateId = candidateIdOf(object.selectedProductId);

  const buy = () => {
    if (!selectedProduct) return;
    if (!actions.addToCart(objectId)) return;
    toast("Added to cart", {
      status: "success",
      description: selectedProduct.title,
      actions: [{ label: "View cart", onClick: () => void navigate("/cart") }],
    });
  };

  // Clicking the header only highlights the row; its buttons keep their own actions.
  const headerStart = (
    <div
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2"
      onClick={() => onFocusRow?.(objectId)}
      role="presentation"
    >
      <ProductTile
        type={selectedProduct?.type ?? ownedProduct?.type ?? object.type}
        imageUrl={(selectedProduct ?? ownedProduct)?.imageUrl}
        size="sm"
      />
      <h3 className="min-w-0 truncate text-headline-medium text-text-primary">{object.name}</h3>
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip color="soft" variant="caption">
          {formatDims(object.dims, unit)}
        </Chip>
        <Chip color="soft" variant="caption">
          {typeLabel}
        </Chip>
        {priceRangeLabel ? (
          <Chip color="soft" variant="caption" title={fuzzyTitle || undefined}>
            <bdi>{priceRangeLabel}</bdi>
          </Chip>
        ) : null}
        {object.minRating !== undefined ? (
          <Chip color="soft" variant="caption">
            <bdi>★ {object.minRating}+</bdi>
          </Chip>
        ) : null}
      </div>
      {learnedText && !running ? (
        <p className="min-w-0 max-w-full basis-full truncate text-caption-1-medium text-text-tertiary" title={learnedText}>
          Learned: {learnedText}
        </p>
      ) : null}
    </div>
  );

  const headerActions = (
    <div
      className="flex flex-wrap items-center gap-2"
      onClick={() => onFocusRow?.(objectId)}
      role="presentation"
    >
      {owned ? null : (
        <Button
          variant="secondary"
          size="small"
          leadingIcon={RiSparkling2Line}
          disabled={!selectedProduct || likeCandidateId === undefined || running}
          onClick={() => likeCandidateId !== undefined && search.searchLike(objectId, likeCandidateId)}
        >
          More like this
        </Button>
      )}
      <Button variant="ghost" size="small" onClick={() => drawer.open(objectId)}>
        Details
      </Button>
      {owned ? (
        <Chip color="orange">Owned</Chip>
      ) : (
        <>
          {running ? (
            <Button variant="secondary" size="small" onClick={() => search.cancel(objectId)}>
              Cancel
            </Button>
          ) : hasSearched ? (
            <Button
              variant="primary"
              size="small"
              leadingIcon={RiRefreshLine}
              title={refineHint}
              onClick={() => search.refine(objectId)}
            >
              Refresh
            </Button>
          ) : (
            <Button variant="secondary" size="small" leadingIcon={RiSearchLine} onClick={() => search.search(objectId)}>
              Search
            </Button>
          )}
          {cartLine ? (
            <Button variant="secondary" size="small" leadingIcon={RiCheckLine} onClick={() => void navigate("/cart")}>
              In cart
            </Button>
          ) : (
            <Button
              variant="primary"
              size="small"
              leadingIcon={RiShoppingCart2Line}
              disabled={!selectedProduct}
              onClick={buy}
            >
              Buy
            </Button>
          )}
        </>
      )}
    </div>
  );

  const standaloneHeader = (
    <div
      className="flex flex-wrap items-center justify-between gap-3"
      onClick={() => onFocusRow?.(objectId)}
      role="presentation"
    >
      {headerStart}
      {headerActions}
    </div>
  );

  let body;
  let isCarousel = false;
  if (owned) {
    body = ownedProduct ? (
      <div className="flex flex-wrap items-end gap-4">
        <ProductCard product={ownedProduct} variant="cart" className={CARD_WIDTH} />
        {object.owned ? (
          <p className="pb-1 text-body-regular text-text-secondary">
            Ordered {object.owned.orderName} · {formatRelativeTime(object.owned.purchasedAt)}
          </p>
        ) : null}
      </div>
    ) : (
      <p className="text-body-regular text-text-secondary">Purchase details are unavailable.</p>
    );
  } else if (running) {
    body = (
      <div className="flex flex-col gap-4">
        <SearchProgress step={object.search.step} likeTitle={search.likeTitle(objectId)} />
        <div className="flex gap-3 overflow-hidden" aria-hidden>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  } else if (candidates.length === 0 && object.search.status === "empty") {
    body = (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-background-secondary-default p-4">
        <p className="text-body-regular text-text-secondary">No matches. Loosen the price range or size.</p>
        <Button variant="secondary" size="small" onClick={() => drawer.open(objectId)}>
          Details
        </Button>
      </div>
    );
  } else if (candidates.length === 0) {
    body = (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-dashed border-border-button-default p-4">
        <div className="flex flex-col gap-0.5">
          <p className="text-body-medium text-text-primary">Not searched yet</p>
          <p className="text-body-regular text-text-secondary">Find matching products in the catalog.</p>
        </div>
        <Button variant="secondary" size="small" leadingIcon={RiSearchLine} onClick={() => search.search(objectId)}>
          Search the catalog
        </Button>
      </div>
    );
  } else {
    isCarousel = true;
    body = (
      <Carousel
        aria-label={`${object.name} matches`}
        header={headerStart}
        actions={headerActions}
        showDots={false}
        gap={12}
        scrollResetKey={object.selectedProductId}
      >
        {candidates.map((product) => (
          <CarouselItem key={product.id} className={CARD_WIDTH}>
            {product.id === object.selectedProductId ? (
              <ProductCard
                product={product}
                variant="selected"
                priceNote={priceNoteFor(product.price)}
                vote={voteOf(product)}
                onVote={(v) => actions.voteProduct(objectId, product.id, v)}
              />
            ) : (
              <ProductCard
                product={product}
                variant="candidate"
                priceNote={priceNoteFor(product.price)}
                vote={voteOf(product)}
                onVote={(v) => actions.voteProduct(objectId, product.id, v)}
                onSelect={() => actions.selectProduct(objectId, product.id)}
              />
            )}
          </CarouselItem>
        ))}
      </Carousel>
    );
  }

  return (
    <section
      id={`object-row-${objectId}`}
      aria-label={object.name}
      className={cx(
        "flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-4",
        highlighted && "ring-2 ring-border-focus-ring",
      )}
    >
      {isCarousel ? null : standaloneHeader}
      {body}
    </section>
  );
}
