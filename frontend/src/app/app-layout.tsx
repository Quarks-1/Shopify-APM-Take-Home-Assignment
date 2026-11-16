import { useMemo, useState } from "react";
import { RiHomeLine, RiLayoutMasonryLine, RiMenuLine, RiSearchEyeLine, RiShoppingCart2Line } from "@remixicon/react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { Counter } from "@/components/base/badges/counter";
import { IconButton } from "@/components/base/buttons/icon-button";
import { Drawer } from "@/components/base/drawer/drawer";
import { DashboardSidebar, type DashboardNavItem } from "@/components/application/dashboard/dashboard-sidebar";
import { ObjectDetailsDrawer } from "@/features/objects/object-details-drawer";
import { useCartCount } from "@/state/app-store";

function navKeyFor(pathname: string): string {
  if (pathname.startsWith("/spaces")) return "spaces";
  if (pathname.startsWith("/find")) return "find";
  if (pathname.startsWith("/cart")) return "cart";
  if (pathname === "/") return "home";
  return "";
}

export function AppLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const cartCount = useCartCount();
  const [menuOpen, setMenuOpen] = useState(false);

  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenuOpen(false);
  }

  const items = useMemo<DashboardNavItem[]>(
    () => [
      { key: "home", label: "Home", icon: RiHomeLine, href: "/" },
      { key: "spaces", label: "Space designer", icon: RiLayoutMasonryLine, href: "/spaces/new" },
      { key: "find", label: "Find objects", icon: RiSearchEyeLine, href: "/find" },
      {
        key: "cart",
        label: "Cart",
        icon: RiShoppingCart2Line,
        href: "/cart",
        badge: cartCount > 0 ? cartCount : undefined,
      },
    ],
    [cartCount],
  );
  const selected = navKeyFor(pathname);
  const onNavigate = (href: string) => navigate(href);

  return (
    <div className="flex min-h-screen bg-background-full">
      <div className="hidden shrink-0 p-3 lg:block">
        <div className="sticky top-3 h-[calc(100vh-24px)]">
          <DashboardSidebar items={items} selected={selected} onNavigate={onNavigate} />
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-separator-border bg-background-full px-4 py-2 lg:hidden">
          <IconButton icon={RiMenuLine} aria-label="Open menu" onClick={() => setMenuOpen(true)} />
          <span className="text-headline-medium text-text-primary">Spaces</span>
          <div className="relative">
            <IconButton icon={RiShoppingCart2Line} aria-label="Cart" onClick={() => navigate("/cart")} />
            {cartCount > 0 && (
              <Counter color="primary" className="pointer-events-none absolute -end-1 -top-1">
                {cartCount}
              </Counter>
            )}
          </div>
        </div>

        <main className="mx-auto flex w-full min-w-0 max-w-[1400px] flex-1 flex-col gap-6 p-4 sm:p-6">
          <Outlet />
        </main>
      </div>

      <Drawer placement="start" isOpen={menuOpen} onOpenChange={setMenuOpen} aria-label="Navigation">
        <DashboardSidebar
          mobile
          items={items}
          selected={selected}
          onClose={() => setMenuOpen(false)}
          onNavigate={onNavigate}
        />
      </Drawer>

      <ObjectDetailsDrawer />
    </div>
  );
}
