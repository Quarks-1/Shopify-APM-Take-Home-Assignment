import { useState, type ComponentType } from "react";
import { Link } from "react-router";
import {
  RiAlertLine,
  RiCheckLine,
  RiDeleteBinLine,
  RiErrorWarningLine,
  RiExternalLinkLine,
  RiInformationLine,
  RiLinksLine,
  RiRefreshLine,
  RiShoppingBag3Line,
} from "@remixicon/react";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Chip } from "@/components/base/badges/chip";
import { IconButton } from "@/components/base/buttons/icon-button";
import { NumberField } from "@/components/base/number-field/number-field";
import { SpinnerGlyph } from "@/components/base/spinner/spinner";
import { toast } from "@/components/base/toast/toast";
import { useObjectDrawer } from "@/features/objects/use-object-drawer";
import { ProductCard } from "@/features/objects/product-card";
import { describeError, type ApiMerchantMessage } from "@/lib/api";
import { formatMinor, formatPrice, formatRelativeTime } from "@/lib/format";
import { useAppActions } from "@/state/app-store";
import { getMerchantTotal, hasMerchantTotals, type CartStoreGroup } from "@/state/selectors";
import { cx } from "@/utils/cx";

const FOCUS = "rounded-md outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring";

type IconComponent = ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;

const MESSAGE_TONE: Record<ApiMerchantMessage["type"], { icon: IconComponent; className: string }> = {
  info: { icon: RiInformationLine, className: "text-notification-information-foreground" },
  warning: { icon: RiAlertLine, className: "text-status-yellow-text" },
  error: { icon: RiErrorWarningLine, className: "text-text-error-primary" },
};

function safeHttps(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).protocol === "https:" ? url : undefined;
  } catch {
    return undefined;
  }
}

/** Spinner sized by the Button's icon slot. */
function PendingIcon({ className }: { className?: string }) {
  return <SpinnerGlyph size="sm" className={cx("text-current", className)} />;
}

function humanize(type: string): string {
  const text = type.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A merchant message. Never collapsible or dismissible: disclosures must stay visible. */
function MerchantMessage({ message, boxed = false }: { message: ApiMerchantMessage; boxed?: boolean }) {
  const tone = MESSAGE_TONE[message.type] ?? MESSAGE_TONE.info;
  const Icon = tone.icon;
  const url = safeHttps(message.url);
  const imageUrl = safeHttps(message.imageUrl);
  return (
    <div
      role="note"
      className={cx("flex items-start gap-2", boxed && "rounded-xl bg-background-secondary-default p-2")}
    >
      <Icon aria-hidden className={cx("mt-0.5 size-4 shrink-0", tone.className)} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p dir="auto" className="text-caption-1-regular text-text-secondary">
          {message.content}
        </p>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={cx(FOCUS, "self-start text-caption-1-medium text-text-primary underline underline-offset-2")}
          >
            Learn more
          </a>
        ) : null}
      </div>
      {imageUrl ? <img src={imageUrl} alt="" className="size-8 shrink-0 rounded-md object-cover" /> : null}
    </div>
  );
}

function StatusChips({ group, onRetry }: { group: CartStoreGroup; onRetry: () => void }) {
  const { merchant, syncing, mode } = group;
  const failed = !syncing && merchant?.status === "error";
  return (
    <>
      {mode === "links" ? (
        <Chip variant="subtle" color="yellow" className="gap-1">
          <RiExternalLinkLine aria-hidden className="size-3.5" />
          Buy links
        </Chip>
      ) : null}
      {mode === "permalink" ? (
        <Chip variant="subtle" color="cyan" className="gap-1">
          <RiLinksLine aria-hidden className="size-3.5" />
          Cart link
        </Chip>
      ) : null}
      {syncing ? (
        <Chip variant="subtle" color="soft" className="gap-1.5" role="status">
          <SpinnerGlyph size="xs" track={false} className="text-current" />
          Updating…
        </Chip>
      ) : null}
      {!syncing && hasMerchantTotals(group) ? (
        <Chip variant="subtle" color="lime" className="gap-1">
          <RiCheckLine aria-hidden className="size-3.5" />
          Merchant totals
        </Chip>
      ) : null}
      {failed ? (
        <>
          <Chip variant="subtle" color="rose" className="gap-1">
            <RiErrorWarningLine aria-hidden className="size-3.5" />
            Couldn't update store cart
          </Chip>
          <Button variant="ghost" size="small" leadingIcon={RiRefreshLine} onClick={onRetry}>
            Retry
          </Button>
        </>
      ) : null}
    </>
  );
}

export function StoreCartBox({ group }: { group: CartStoreGroup }) {
  const { store, lines, subtotal, itemCount, checkoutOpenedAt, merchant, syncing, mode, notices } = group;
  const { setQty, removeLine, checkout, syncStore } = useAppActions();
  const drawer = useObjectDrawer();
  const [pending, setPending] = useState(false);

  const merchantTotals = hasMerchantTotals(group) && merchant?.currency ? merchant : undefined;
  const grandTotal = merchantTotals ? getMerchantTotal(merchantTotals) : undefined;
  const failed = !syncing && merchant?.status === "error";

  const buyNow = async () => {
    // Opened synchronously, inside the click, so popup blockers allow it; it navigates once the URL is known.
    const tab = window.open("about:blank", "_blank");
    setPending(true);
    try {
      const { url, kind } = await checkout(store.id);
      const description = kind === "permalink" ? "Shopify cart opened in a new tab." : "Shopify checkout opened in a new tab.";
      if (tab && !tab.closed) {
        tab.opener = null;
        tab.location.href = url;
        toast(`Checkout opened for ${store.name}`, { description });
      } else {
        // The popup was blocked: a click on the toast's button is a fresh user gesture.
        toast(`Checkout ready for ${store.name}`, {
          description: "Your browser blocked the new tab.",
          duration: Infinity,
          actions: [
            { label: "Open checkout", variant: "primary", onClick: () => void window.open(url, "_blank", "noopener,noreferrer") },
          ],
        });
      }
    } catch (error) {
      tab?.close();
      toast.error(`Couldn't open checkout for ${store.name}`, { description: describeError(error) });
    } finally {
      setPending(false);
    }
  };

  return (
    <section
      aria-label={store.name}
      className="flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5"
    >
      <Carousel
        aria-label={`${store.name} items`}
        showDots={false}
        gap={12}
        header={
          <header className="flex min-w-0 flex-col gap-2">
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
              <StatusChips group={group} onRetry={() => syncStore(store.id)} />
              {checkoutOpenedAt ? (
                <Chip variant="subtle" color="lime" className="gap-1">
                  <RiCheckLine aria-hidden className="size-3.5" />
                  Checkout opened · {formatRelativeTime(checkoutOpenedAt)}
                </Chip>
              ) : null}
            </div>
            {failed && merchant?.error ? (
              <p className="text-caption-1-regular text-text-error-primary">{merchant.error.message}</p>
            ) : null}
            {mode === "links" ? (
              <p className="text-caption-1-regular text-text-secondary">
                This store doesn't take carts from here. Buy each item on the store's site.
              </p>
            ) : null}
            {mode === "permalink" ? (
              <p className="text-caption-1-regular text-text-secondary">
                Buy now opens one Shopify cart on the store's site with every item below.
              </p>
            ) : null}
            {notices.length > 0 ? (
              <div className="flex max-w-xl flex-col gap-2">
                {notices.map((message, i) => (
                  <MerchantMessage key={`${message.code ?? message.type}-${i}`} message={message} />
                ))}
              </div>
            ) : null}
          </header>
        }
        actions={
          <>
            <div className="me-2 flex flex-col items-end">
              <span className="text-caption-1-medium text-text-tertiary">
                {merchantTotals && grandTotal ? grandTotal.displayText || humanize(grandTotal.type) : "Estimated subtotal"}
              </span>
              <bdi dir="ltr" className="text-headline-medium text-text-primary">
                {merchantTotals && grandTotal && merchantTotals.currency
                  ? formatMinor(grandTotal.amount, merchantTotals.currency)
                  : formatPrice(subtotal)}
              </bdi>
            </div>
            {mode !== "links" ? (
              <Button
                variant="primary"
                leadingIcon={pending ? PendingIcon : RiShoppingBag3Line}
                onClick={() => void buyNow()}
                disabled={pending || syncing}
                aria-busy={pending || undefined}
              >
                {pending ? "Opening…" : "Buy now"}
              </Button>
            ) : null}
          </>
        }
      >
        {lines.map(({ line, product, object, spaces, disclosures, buyUrl }) => (
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
                  {buyUrl ? (
                    <div className="flex flex-col gap-1">
                      <ButtonLink
                        href={buyUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        variant="secondary"
                        size="small"
                        trailingIcon={RiExternalLinkLine}
                      >
                        Buy on store
                      </ButtonLink>
                      {mode !== "links" ? (
                        <span className="text-caption-1-regular text-text-tertiary">
                          Not in the store cart. Buy it on its own.
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  {disclosures.length > 0 ? (
                    <div className="flex flex-col gap-2">
                      {disclosures.map((message, i) => (
                        <MerchantMessage key={`${message.code ?? "disclosure"}-${i}`} message={message} boxed />
                      ))}
                    </div>
                  ) : null}
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
      {merchantTotals && merchantTotals.currency ? (
        <dl className="flex flex-wrap justify-end gap-x-6 gap-y-1 border-t border-separator-border pt-3">
          {merchantTotals.totals.map((total, i) => (
            <div key={`${total.type}-${i}`} className="flex items-baseline gap-2">
              <dt className="text-caption-1-medium text-text-tertiary">{total.displayText || humanize(total.type)}</dt>
              <dd>
                <bdi dir="ltr" className="text-body-medium text-text-primary">
                  {formatMinor(total.amount, merchantTotals.currency ?? "USD")}
                </bdi>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
