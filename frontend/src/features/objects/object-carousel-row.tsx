import { RiCheckLine, RiSearchLine, RiShoppingCart2Line } from "@remixicon/react";
import { useNavigate } from "react-router";
import { Chip } from "@/components/base/badges/chip";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { toast } from "@/components/base/toast/toast";
import { formatDims, formatPrice, formatRelativeTime } from "@/lib/format";
import { OBJECT_TYPE_OPTIONS } from "@/mocks/types";
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
  const running = search.isRunning(objectId) || object.search.status === "running";
  const cartLine = getCartLineForObject(state, objectId);
  const selectedProduct = getSelectedProduct(state, object);
  const ownedProduct = object.owned ? getProduct(state, object.owned.productId) : undefined;

  const candidates = getCandidateProducts(state, object).sort(
    (a, b) => Number(b.id === object.selectedProductId) - Number(a.id === object.selectedProductId),
  );

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
        {object.priceLimit !== undefined ? (
          <Chip color="soft" variant="caption">
            <bdi>≤ {formatPrice(object.priceLimit)}</bdi>
          </Chip>
        ) : null}
        {object.minRating !== undefined ? (
          <Chip color="soft" variant="caption">
            <bdi>★ {object.minRating}+</bdi>
          </Chip>
        ) : null}
      </div>
    </div>
  );

  const headerActions = (
    <div
      className="flex flex-wrap items-center gap-2"
      onClick={() => onFocusRow?.(objectId)}
      role="presentation"
    >
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
        <SearchProgress step={object.search.step} />
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
        <p className="text-body-regular text-text-secondary">No matches. Loosen the price limit or size.</p>
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
              <ProductCard product={product} variant="selected" />
            ) : (
              <ProductCard
                product={product}
                variant="candidate"
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
