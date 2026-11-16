import { useEffect, useState } from "react";
import { RiHomeLine, RiLayoutGridLine } from "@remixicon/react";
import {
  DashboardSidebar,
  type DashboardNavItem,
} from "@/components/application/dashboard/dashboard-sidebar";
import { SpacesOverview } from "@/components/application/spaces/spaces-overview";
import { SpaceEditor } from "@/components/application/space-editor/space-editor";
import { ThemeToggle } from "@/components/application/theme/theme-toggle";
import { editorHref, overviewHref, parseAppRoute, type AppRoute } from "@/lib/app-routing";

const NAV_ITEMS: DashboardNavItem[] = [
  { key: "overview", label: "Overview", icon: RiHomeLine, href: overviewHref() },
  { key: "editor", label: "Space editor", icon: RiLayoutGridLine, href: editorHref() },
];

function readRoute(): AppRoute {
  return parseAppRoute(window.location.hash);
}

export default function App() {
  const [route, setRoute] = useState(readRoute);

  useEffect(() => {
    const syncRoute = () => setRoute(readRoute());
    window.addEventListener("hashchange", syncRoute);
    if (!window.location.hash) {
      window.location.replace(overviewHref());
    }
    return () => window.removeEventListener("hashchange", syncRoute);
  }, []);

  const sidebarSelected = route.view === "editor" ? "editor" : "overview";

  return (
    <div className="flex min-h-screen bg-background-full">
      <div className="hidden shrink-0 p-3 lg:block">
        <div className="sticky top-3 h-[calc(100vh-24px)]">
          <DashboardSidebar selected={sidebarSelected} items={NAV_ITEMS} />
        </div>
      </div>

      <main
        id={route.view === "overview" ? "overview" : "space-editor"}
        className="mx-auto flex min-w-0 w-full max-w-[1300px] flex-1 flex-col gap-6 p-4 sm:p-6"
      >
        <div className="flex justify-end lg:hidden">
          <ThemeToggle appearance="segmented" />
        </div>
        {route.view === "editor" ? (
          <SpaceEditor initialSpaceId={route.spaceId} />
        ) : (
          <SpacesOverview />
        )}
      </main>
    </div>
  );
}