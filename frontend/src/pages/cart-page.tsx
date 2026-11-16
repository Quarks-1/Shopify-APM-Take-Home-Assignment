import { useNavigate } from "react-router";
import { RiShoppingCart2Line } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { CartSummary } from "@/features/cart/cart-summary";
import { StoreCartBox } from "@/features/cart/store-cart-box";
import { useAppState } from "@/state/app-store";
import { getCartByStore } from "@/state/selectors";

export function CartPage() {
  const state = useAppState();
  const navigate = useNavigate();
  const groups = getCartByStore(state);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-title-1-medium text-text-primary">Cart</h1>
          <p className="text-body-regular text-text-secondary">Everything you're buying, grouped by store.</p>
        </div>
      </header>

      {groups.length > 0 ? (
        <>
          <CartSummary />
          <div className="flex flex-col gap-6">
            {groups.map((group) => (
              <StoreCartBox key={group.store.id} group={group} />
            ))}
          </div>
        </>
      ) : (
        <section className="flex flex-col items-center gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-8 text-center">
          <RiShoppingCart2Line aria-hidden className="size-10 text-foreground-icon-tertiary" />
          <div className="flex flex-col gap-1">
            <h2 className="text-title-3-semibold text-text-primary">Your cart is empty</h2>
            <p className="text-body-regular text-text-secondary">
              Pick products on a space or the Find objects page, then press Buy.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="primary" onClick={() => navigate("/find")}>
              Find objects
            </Button>
            <Button variant="secondary" onClick={() => navigate("/")}>
              Go home
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
