import { RiDeleteBinLine, RiErrorWarningLine } from "@remixicon/react";
import { AlertDialog, ModalTrigger } from "@/components/base/modal/modal";
import { Button } from "@/components/base/buttons/button";
import { toast } from "@/components/base/toast/toast";
import { formatPrice } from "@/lib/format";
import { useAppActions, useAppState } from "@/state/app-store";
import { getCartByStore, getCartCount, getCartTotal } from "@/state/selectors";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-caption-1-medium text-text-tertiary">{label}</span>
      <span className="text-title-2-medium text-text-primary">{value}</span>
    </div>
  );
}

export function CartSummary() {
  const state = useAppState();
  const { clearCart } = useAppActions();
  const itemCount = getCartCount(state);
  const storeCount = getCartByStore(state).length;
  const total = getCartTotal(state);

  return (
    <section
      aria-label="Cart summary"
      className="flex flex-col gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-5"
    >
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
        <Stat label="Items" value={itemCount} />
        <Stat label="Stores" value={storeCount} />
        <Stat label="Total" value={formatPrice(total)} />
        <div className="ms-auto">
          <ModalTrigger>
            <Button variant="ghost" leadingIcon={RiDeleteBinLine}>
              Clear cart
            </Button>
            <AlertDialog
              title="Clear cart?"
              description="This removes every item from the cart across all stores."
              icon={RiErrorWarningLine}
              confirmLabel="Clear cart"
              variant="danger"
              onConfirm={() => {
                clearCart();
                toast("Cart cleared");
              }}
            />
          </ModalTrigger>
        </div>
      </div>
      <p className="text-caption-1-medium text-text-tertiary">Each store checks out separately.</p>
    </section>
  );
}
