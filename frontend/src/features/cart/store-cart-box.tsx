import { Link } from "react-router";
import { RiCheckLine, RiDeleteBinLine, RiShoppingBag3Line } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Chip } from "@/components/base/badges/chip";
import { IconButton } from "@/components/base/buttons/icon-button";
import { NumberField } from "@/components/base/number-field/number-field";
import { toast } from "@/components/base/toast/toast";
import { useObjectDrawer } from "@/features/objects/use-object-drawer";
import { ProductCard } from "@/features/objects/product-card";
import { formatPrice, formatRelativeTime } from "@/lib/format";
import { useAppActions } from "@/state/app-store";
import type { CartStoreGroup } from "@/state/selectors";
import { cx } from "@/utils/cx";

const FOCUS = "rounded-md outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring";

export function StoreCartBox({ group }: { group: CartStoreGroup }) {
  const { store, lines, subtotal, itemCount, checkoutOpenedAt } = group;
  const { setQty, removeLine, markCheckoutOpened } = useAppActions();
  const drawer = useObjectDrawer();

  const buyNow = () => {
    window.open(`/checkout/mock/${store.id}`, "_blank", "noopener,noreferrer");
    markCheckoutOpened(store.id);
    toast(`Checkout opened for ${store.name}`, { description: "Mock checkout opened in a new tab." });
  };

  return (
    <section
      aria-label={store.name}
      className="flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5"
    >
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <h2 className="text-title-3-semibold text-text-primary">{store.name}</h2>
          <div className="flex flex-col gap-1">
            <bdi dir="ltr" className="text-caption-1-medium text-text-tertiary">
              {store.domain}
            </bdi>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Chip variant="subtle" color="soft">
              {itemCount} items
            </Chip>
            {checkoutOpenedAt ? (
              <Chip variant="subtle" color="lime" className="gap-1">
                <RiCheckLine aria-hidden className="size-3.5" />
                Checkout opened · {formatRelativeTime(checkoutOpenedAt)}
              </Chip>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <bdi dir="ltr" className="text-headline-medium text-text-primary">
            {formatPrice(subtotal)}
          </bdi>
          <Button variant="primary" leadingIcon={RiShoppingBag3Line} onClick={buyNow}>
            Buy now
          </Button>
        </div>
      </header>

      <Carousel aria-label={`${store.name} items`} showDots={false} gap={12}>
        {lines.map(({ line, product, object, spaces }) => (
          <CarouselItem key={line.id} className="w-60">
            <ProductCard
              product={product}
              variant="cart"
              footer={
                <div className="flex flex-col gap-3 pt-3">
                  <div className="flex items-center justify-between gap-2">
                    <NumberField
                      aria-label="Quantity"
                      className="min-w-0 flex-1"
                      size="small"
                      minValue={0}
                      maxValue={20}
                      value={line.qty}
                      onChange={(next) => {
                        if (Number.isFinite(next)) setQty(line.id, Math.round(next));
                      }}
                    />
                    <IconButton
                      icon={RiDeleteBinLine}
                      size="small"
                      aria-label={`Remove ${product.title}`}
                      onClick={() => removeLine(line.id)}
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {object ? (
                      <button
                        type="button"
                        onClick={() => drawer.open(object.id)}
                        className={cx(FOCUS, "cursor-pointer")}
                      >
                        <Chip variant="caption" color="neutral">
                          {object.name}
                        </Chip>
                      </button>
                    ) : null}
                    {spaces.map((space) => (
                      <Link key={space.id} to={`/spaces/${space.id}`} className={FOCUS}>
                        <Chip variant="caption" color="soft">
                          {space.name}
                        </Chip>
                      </Link>
                    ))}
                  </div>
                </div>
              }
            />
          </CarouselItem>
        ))}
      </Carousel>
    </section>
  );
}
