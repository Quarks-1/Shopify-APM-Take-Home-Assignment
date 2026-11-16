import { RouterProvider as AriaRouterProvider } from "react-aria-components";
import { Link, Outlet, ScrollRestoration, useHref, useNavigate } from "react-router";
import { Toaster } from "@/components/base/toast/toast";

/** Root of the route tree: wires React Aria links to the router and hosts the Toaster. */
export function RootRoute() {
  const navigate = useNavigate();
  return (
    <AriaRouterProvider navigate={(to, opts) => navigate(to, opts)} useHref={useHref}>
      <Outlet />
      <ScrollRestoration />
      <Toaster position="bottom-end" />
    </AriaRouterProvider>
  );
}

export function RouteError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background-full p-6 text-center">
      <h1 className="text-title-2-medium text-text-primary">Something went wrong</h1>
      <p className="text-body-regular text-text-secondary">This page couldn't be shown.</p>
      <Link
        to="/"
        className="rounded-md text-body-medium text-text-primary underline outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
      >
        Back to home
      </Link>
    </div>
  );
}
