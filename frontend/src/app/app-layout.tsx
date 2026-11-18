import { useMemo, useState, useSyncExternalStore } from "react";
import { RiHomeLine, RiLayoutMasonryLine, RiMenuLine, RiSearchEyeLine, RiShoppingCart2Line } from "@remixicon/react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { Counter } from "@/components/base/badges/counter";
import { IconButton } from "@/components/base/buttons/icon-button";
import { Drawer } from "@/components/base/drawer/drawer";
import { DashboardSidebar, type DashboardNavItem } from "@/components/application/dashboard/dashboard-sidebar";
import { AppErrorView, AppLoadingView } from "@/app/load-states";
import { ObjectDetailsDrawer } from "@/features/objects/object-details-drawer";
import { useAppState, useCartCount } from "@/state/app-store";
import { useAppStatus } from "@/state/store-context";

const DESKTOP_MEDIA_QUERY = "(min-width: 1024px)";

function subscribeDesktopViewport(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const mql = window.matchMedia(DESKTOP_MEDIA_QUERY);
  mql.addEventListener("change", onChange);
  window.addEventListener("resize", onChange);
  return () => {
    mql.removeEventListener("change", onChange);
    window.removeEventListener("resize", onChange);
  };
}

function getDesktopViewportSnapshot(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia(DESKTOP_MEDIA_QUERY).matches || window.innerWidth >= 1024;
}

function getServerDesktopSnapshot(): boolean {
  return true;
}

function navKeyFor(pathname: string): string {
  if (pathname === "/spaces" || pathname.startsWith("/spaces/")) return "spaces";
  if (pathname === "/find" || pathname.startsWith("/find/")) return "find";
  if (pathname === "/cart" || pathname.startsWith("/cart/")) return "cart";
  if (pathname === "/") return "home";
  return "";
}

function extractSpaceSegment(pathname: string): string {
  if (!pathname.startsWith("/spaces/")) return "";
  const raw = pathname.slice("/spaces/".length).split("/")[0] ?? "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function AppLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const state = useAppState();
  const cartCount = useCartCount();
  const { status, retry } = useAppStatus();
  const isDesktop = useSyncExternalStore(
    subscribeDesktopViewport,
    getDesktopViewportSnapshot,
    getServerDesktopSnapshot,
  );
  const [menuOpen, setMenuOpen] = useState(false);
  // Pages need spaces and objects from the backend; the dev status page is what diagnoses a missing one.
  const ready = status.phase === "ready" || pathname.startsWith("/dev/");

  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenuOpen(false);
  }
  if (isDesktop && menuOpen) {
    setMenuOpen(false);
  }

  const activeSpaceId = extractSpaceSegment(pathname);
  const [lastSpaceId, setLastSpaceId] = useState<string | null>(null);
  if (activeSpaceId && activeSpaceId !== "new" && activeSpaceId !== lastSpaceId) {
    setLastSpaceId(activeSpaceId);
  }

  const targetSpace =
    (lastSpaceId ? state.spaces.find((s) => s.id === lastSpaceId) : undefined) ?? state.spaces[0];
  const spacesHref = targetSpace ? `/spaces/${encodeURIComponent(targetSpace.id)}` : "/spaces/new";

  const items = useMemo<DashboardNavItem[]>(
    () => [
      { key: "home", label: "Home", icon: RiHomeLine, href: "/" },
      { key: "spaces", label: "Space designer", icon: RiLayoutMasonryLine, href: spacesHref },
      { key: "find", label: "Find objects", icon: RiSearchEyeLine, href: "/find" },
      {
        key: "cart",
        label: "Cart",
        icon: RiShoppingCart2Line,
        href: "/cart",
        badge: cartCount > 0 ? cartCount : undefined,
      },
    ],
    [cartCount, spacesHref],
  );
  const selected = navKeyFor(pathname);
  const onNavigate = (href: string) => {
    setMenuOpen(false);
    navigate(href);
  };

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

        <main className="flex w-full min-w-0 flex-1 flex-col gap-6 p-4 sm:p-6">
          {ready ? (
            <Outlet />
          ) : status.phase === "error" ? (
            <AppErrorView error={status.error} onRetry={retry} />
          ) : (
            <AppLoadingView />
          )}
        </main>
      </div>

      <Drawer
        placement="start"
        isOpen={menuOpen && !isDesktop}
        onOpenChange={setMenuOpen}
        aria-label="Navigation"
        overlayClassName="lg:hidden"
      >
        <DashboardSidebar
          mobile
          items={items}
          selected={selected}
          onClose={() => setMenuOpen(false)}
          onNavigate={onNavigate}
        />
      </Drawer>

      {/* Only once loaded: it closes itself for object ids it can't find. */}
      {status.phase === "ready" ? <ObjectDetailsDrawer /> : null}
    </div>
  );
}
