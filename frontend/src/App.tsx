import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RiCheckboxCircleFill,
  RiCodeSSlashLine,
  RiDatabase2Fill,
  RiErrorWarningLine,
  RiHomeLine,
  RiPulseFill,
  RiRefreshLine,
  RiRouteFill,
  RiSearchLine,
  RiServerFill,
} from "@remixicon/react";
import {
  DashboardSidebar,
  type DashboardNavItem,
} from "@/components/application/dashboard/dashboard-sidebar";
import { StatCards, type Stat } from "@/components/application/dashboard/stat-cards";
import { ThemeToggle } from "@/components/application/theme/theme-toggle";
import { Chip } from "@/components/base/badges/chip";
import { StatusDot } from "@/components/base/badges/status-dot";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Skeleton, SkeletonText } from "@/components/base/skeleton/skeleton";
import { Spinner } from "@/components/base/spinner/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
} from "@/components/base/table/table";
import { Tab, TabList, TabPanel, Tabs } from "@/components/base/tabs/tabs";
import { fetchWorkspaceSnapshot, type WorkspaceSnapshot } from "@/lib/api";
import { cx } from "@/utils/cx";

const NAV_ITEMS: DashboardNavItem[] = [
  { key: "overview", label: "Overview", icon: RiHomeLine, href: "#overview" },
  { key: "endpoints", label: "API Routes", icon: RiCodeSSlashLine, href: "#endpoints", badge: 3 },
];

export default function App() {
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [routeFilter, setRouteFilter] = useState("");

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);

    try {
      const next = await fetchWorkspaceSnapshot();
      setSnapshot(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reach Django backend");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchWorkspaceSnapshot(controller.signal)
      .then((next) => {
        setSnapshot(next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Unable to reach Django backend");
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  const stats = useMemo<Stat[]>(() => {
    const healthy = snapshot?.health.status === "ok" && !error;
    const endpointCount = snapshot?.overview.endpoints.length ?? 0;

    return [
      {
        icon: RiPulseFill,
        label: "API health",
        value: healthy ? "Healthy" : error ? "Offline" : "Checking",
        delta: healthy ? "200 OK" : "Error",
        deltaColor: healthy ? "lime" : "rose",
        tone: "emerald",
        caption: "GET /api/health/",
        hint: "Live health probe against the Django JSON endpoint via the Vite /api proxy.",
      },
      {
        icon: RiServerFill,
        label: "Backend runtime",
        value: snapshot?.health.framework ?? "Django",
        delta: snapshot?.overview.environment ?? "dev",
        deltaColor: "neutral",
        tone: "blue",
        caption: snapshot?.health.service ?? "shopify-apm-backend",
        hint: "Framework version and active runtime mode reported by Django.",
      },
      {
        icon: RiDatabase2Fill,
        label: "Database status",
        value: snapshot?.health.database === "connected" ? "Connected" : "Pending",
        delta: `${snapshot?.latencyMs ?? 0} ms`,
        deltaColor: snapshot?.health.database === "connected" ? "lime" : "neutral",
        tone: "purple",
        caption: "Round-trip latency",
        hint: "SQLite connection verified on each health request using connection.ensure_connection().",
      },
      {
        icon: RiRouteFill,
        label: "Registered routes",
        value: String(endpointCount),
        delta: "JSON",
        deltaColor: "lime",
        tone: "orange",
        caption: "Active starter routes",
        hint: "Endpoints exposed by the Django backend and surfaced through /api/overview/.",
      },
    ];
  }, [error, snapshot]);

  const filteredEndpoints = useMemo(() => {
    const rows = snapshot?.overview.endpoints ?? [];
    const needle = routeFilter.trim().toLocaleLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (item) =>
        item.path.toLocaleLowerCase().includes(needle) ||
        item.description.toLocaleLowerCase().includes(needle) ||
        item.method.toLocaleLowerCase().includes(needle),
    );
  }, [routeFilter, snapshot]);

  return (
    <div className="flex min-h-screen bg-background-full">
      <div className="hidden shrink-0 p-3 lg:block">
        <div className="sticky top-3 h-[calc(100vh-24px)]">
          <DashboardSidebar selected="overview" items={NAV_ITEMS} />
        </div>
      </div>

      <main
        id="overview"
        className="mx-auto flex min-w-0 w-full max-w-[1300px] flex-1 flex-col gap-6 p-4 sm:p-6"
      >
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex items-center gap-2">
              <StatusDot color={error ? "yellow" : "green"} />
              <span className="text-caption-1-medium text-text-tertiary">
                {snapshot?.overview.project ?? "Shopify APM Workspace"}
              </span>
            </div>
            <h1 className="text-title-1-medium text-text-primary">Overview</h1>
            <p className="text-body-regular text-text-secondary">
              Django JSON API wired to a Vite, React, TypeScript, and BoardUI frontend.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <ThemeToggle appearance="segmented" />
            <Button
              variant="secondary"
              leadingIcon={refreshing ? undefined : RiRefreshLine}
              disabled={loading || refreshing}
              onClick={() => void handleRefresh()}
            >
              {refreshing ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner size="xs" aria-hidden />
                  <span>Refreshing</span>
                </span>
              ) : (
                "Refresh API"
              )}
            </Button>
          </div>
        </header>

        {error ? (
          <section
            role="alert"
            className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-border-error-default bg-background-primary-default p-5"
          >
            <div className="flex items-start gap-3">
              <RiErrorWarningLine
                className="size-5 shrink-0 text-foreground-icon-error"
                aria-hidden
              />
              <div className="flex flex-col gap-1">
                <h2 className="text-headline-medium text-text-primary">
                  Backend connection error
                </h2>
                <p className="text-body-regular text-text-error-primary">{error}</p>
                <p className="text-body-2-regular text-text-secondary">
                  Ensure the Django server is running on{" "}
                  <span dir="ltr" className="font-mono">
                    http://127.0.0.1:8000
                  </span>
                  .
                </p>
              </div>
            </div>
            <Button variant="primary" size="small" onClick={() => void handleRefresh()}>
              Retry connection
            </Button>
          </section>
        ) : null}

        {loading && !snapshot ? (
          <div aria-busy className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, idx) => (
              <div
                key={idx}
                className="flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5"
              >
                <Skeleton shape="rect" className="size-10" />
                <SkeletonText lines={2} />
              </div>
            ))}
          </div>
        ) : (
          <StatCards variant="footer" stats={stats} />
        )}

        <section
          id="endpoints"
          className="flex flex-col gap-5 rounded-3xl border border-border-button-default bg-background-primary-default p-5 sm:p-6"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <h2 className="text-title-2-medium text-text-primary">Backend integration</h2>
              <p className="text-body-regular text-text-secondary">
                Live routes and JSON telemetry returned by the Django starter application.
              </p>
            </div>
            {snapshot ? (
              <div className="flex items-center gap-2">
                <RiCheckboxCircleFill
                  className="size-4 shrink-0 text-foreground-icon-secondary"
                  aria-hidden
                />
                <span className="text-caption-1-medium text-text-tertiary">
                  Last checked{" "}
                  <bdi dir="ltr">{new Date(snapshot.health.timestamp).toLocaleTimeString()}</bdi>
                </span>
              </div>
            ) : null}
          </div>

          <Tabs defaultSelectedKey="routes">
            <TabList aria-label="Backend inspection views">
              <Tab id="routes" count={filteredEndpoints.length}>
                API Routes
              </Tab>
              <Tab id="payload">Health Payload</Tab>
            </TabList>

            <TabPanel id="routes" className="flex flex-col gap-4 pt-2 outline-none">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <Input
                  size="small"
                  value={routeFilter}
                  onChange={setRouteFilter}
                  leadingIcon={RiSearchLine}
                  placeholder="Filter endpoints by path or description"
                  aria-label="Filter endpoints"
                  className="w-full sm:max-w-xs"
                />
                <Chip variant="caption" color="soft">
                  Proxy /api to 127.0.0.1:8000
                </Chip>
              </div>

              <div className="overflow-hidden rounded-2xl border border-border-table">
                <Table aria-label="Registered Django API endpoints">
                  <TableHeader>
                    <TableColumn id="method" isRowHeader>
                      Method
                    </TableColumn>
                    <TableColumn id="path">Path</TableColumn>
                    <TableColumn id="description">Description</TableColumn>
                    <TableColumn id="status">Status</TableColumn>
                  </TableHeader>
                  <TableBody
                    items={filteredEndpoints}
                    renderEmptyState={() => (
                      <div className="p-6 text-center text-body-regular text-text-tertiary">
                        {loading ? "Loading endpoints..." : "No matching API endpoints."}
                      </div>
                    )}
                  >
                    {(endpoint) => (
                      <TableRow id={endpoint.id}>
                        <TableCell>
                          <Chip variant="bold" color="blue">
                            {endpoint.method}
                          </Chip>
                        </TableCell>
                        <TableCell>
                          <span dir="ltr" className="font-mono text-body-2-medium text-text-primary">
                            {endpoint.path}
                          </span>
                        </TableCell>
                        <TableCell>
                          <span className="text-body-regular text-text-secondary">
                            {endpoint.description}
                          </span>
                        </TableCell>
                        <TableCell>
                          <span className="inline-flex items-center gap-2">
                            <StatusDot color="green" />
                            <span className="text-body-medium text-text-primary">
                              {endpoint.status}
                            </span>
                          </span>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </TabPanel>

            <TabPanel id="payload" className="pt-2 outline-none">
              <div
                className={cx(
                  "overflow-x-auto rounded-2xl border border-separator-border bg-background-secondary-default p-4",
                )}
              >
                <pre
                  dir="ltr"
                  className="font-mono text-body-2-regular text-text-primary"
                >
                  {snapshot
                    ? JSON.stringify(snapshot, null, 2)
                    : "Waiting for /api/health/ response..."}
                </pre>
              </div>
            </TabPanel>
          </Tabs>
        </section>
      </main>
    </div>
  );
}
