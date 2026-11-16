import { useNavigate, useParams } from "react-router";
import { RiInformationLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import {
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
} from "@/components/base/table/table";
import { AppErrorView, AppLoadingView } from "@/app/load-states";
import { formatPrice } from "@/lib/format";
import { useAppState } from "@/state/app-store";
import { getCartByStore } from "@/state/selectors";
import { useAppStatus } from "@/state/store-context";

const FREE_SHIPPING_THRESHOLD = 500;
const FLAT_SHIPPING = 25;
const TAX_RATE = 0.08;

function TotalRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className={strong ? "text-title-3-semibold text-text-primary" : "text-body-regular text-text-secondary"}>
        {label}
      </span>
      <bdi dir="ltr" className={strong ? "text-title-3-semibold text-text-primary text-end" : "text-body-medium text-text-primary text-end"}>
        {value}
      </bdi>
    </div>
  );
}

export function MockCheckoutPage() {
  const { storeId } = useParams();
  const navigate = useNavigate();
  const { status, retry } = useAppStatus();
  const group = getCartByStore(useAppState()).find((g) => g.store.id === storeId);

  if (status.phase !== "ready") {
    return (
      <div className="min-h-screen bg-background-full">
        <div className="mx-auto flex max-w-3xl flex-col gap-6 p-4 sm:p-8">
          {status.phase === "error" ? <AppErrorView error={status.error} onRetry={retry} /> : <AppLoadingView />}
        </div>
      </div>
    );
  }

  if (!group) {
    return (
      <div className="min-h-screen bg-background-full">
        <div className="mx-auto flex max-w-3xl flex-col gap-6 p-4 sm:p-8">
          <section className="flex flex-col items-start gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5">
            <h1 className="text-title-2-medium text-text-primary">Nothing to check out for this store</h1>
            <Button variant="secondary" onClick={() => navigate("/cart")}>
              Back to cart
            </Button>
          </section>
        </div>
      </div>
    );
  }

  const { store, lines, subtotal } = group;
  const shipping = subtotal >= FREE_SHIPPING_THRESHOLD ? 0 : FLAT_SHIPPING;
  const tax = subtotal * TAX_RATE;
  const total = subtotal + shipping + tax;

  return (
    <div className="min-h-screen bg-background-full">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 p-4 sm:p-8">
        <section className="flex items-start gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-5">
          <RiInformationLine aria-hidden className="mt-0.5 size-5 shrink-0 text-foreground-icon-secondary" />
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="text-body-medium text-text-primary">Mock checkout</h2>
            <p className="text-body-regular text-text-secondary">
              This tab stands in for the Shopify checkout that UCP will open later. Nothing is charged.
            </p>
          </div>
        </section>

        <header className="flex flex-col gap-1">
          <h1 className="text-title-1-medium text-text-primary">{store.name}</h1>
          <bdi dir="ltr" className="text-caption-1-medium text-text-tertiary">
            {store.domain}
          </bdi>
        </header>

        <div className="overflow-x-auto rounded-3xl border border-border-button-default bg-background-primary-default">
          <Table aria-label={`${store.name} order`}>
            <TableHeader>
              <TableColumn isRowHeader>Product</TableColumn>
              <TableColumn>For</TableColumn>
              <TableColumn className="text-end">Qty</TableColumn>
              <TableColumn className="text-end">Price</TableColumn>
              <TableColumn className="text-end">Line total</TableColumn>
            </TableHeader>
            <TableBody>
              {lines.map(({ line, product, object }) => (
                <TableRow key={line.id}>
                  <TableCell>{product.title}</TableCell>
                  <TableCell>{object?.name ?? "-"}</TableCell>
                  <TableCell className="text-end">{line.qty}</TableCell>
                  <TableCell className="text-end">
                    <bdi dir="ltr">{formatPrice(product.price)}</bdi>
                  </TableCell>
                  <TableCell className="text-end">
                    <bdi dir="ltr">{formatPrice(product.price * line.qty)}</bdi>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <section className="flex flex-col gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-5">
          <TotalRow label="Subtotal" value={formatPrice(subtotal)} />
          <TotalRow label="Shipping" value={shipping === 0 ? "Free" : formatPrice(shipping)} />
          <TotalRow label="Estimated tax (8%)" value={formatPrice(Number(tax.toFixed(2)))} />
          <div className="border-t border-separator-border pt-3">
            <TotalRow label="Total" value={formatPrice(Number(total.toFixed(2)))} strong />
          </div>
        </section>

        <footer className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1">
            <Button variant="primary" disabled>
              Pay now
            </Button>
            <span className="text-caption-1-medium text-text-tertiary">Disabled in the mock</span>
          </div>
          <Button variant="secondary" onClick={() => navigate("/cart")}>
            Back to cart
          </Button>
        </footer>
      </div>
    </div>
  );
}
