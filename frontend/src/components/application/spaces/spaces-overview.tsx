import { useCallback, useEffect, useState } from "react";
import { RiArrowRightLine, RiErrorWarningLine, RiLayoutGridLine } from "@remixicon/react";
import { editorHref } from "@/lib/app-routing";
import { fetchSpaces, type SpaceSummary } from "@/lib/spaces";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Chip } from "@/components/base/badges/chip";
import { Skeleton, SkeletonText } from "@/components/base/skeleton/skeleton";
import { Spinner } from "@/components/base/spinner/spinner";
import { cx } from "@/utils/cx";

const SPACE_TYPE_LABEL: Record<SpaceSummary["space_type"], string> = {
  floor: "Floor",
  shelf: "Shelf",
  wall: "Wall",
};

function formatObjectCount(count: number): string {
  return count === 1 ? "1 object" : `${count} objects`;
}

export function SpacesOverview() {
  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchSpaces(signal);
      if (signal?.aborted) return;
      setSpaces(payload.spaces);
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : "Unable to load spaces");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
  <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-title-1-medium text-text-primary">Your spaces</h1>
          <p className="text-body-regular text-text-secondary">
            Open a space to edit objects on the floor plan. Right-click an object to change its attributes.
          </p>
        </div>
        <Button
          variant="secondary"
          size="small"
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? (
            <span className="inline-flex items-center gap-2">
              <Spinner size="xs" aria-hidden />
              <span>Refreshing</span>
            </span>
          ) : (
            "Refresh spaces"
          )}
        </Button>
      </header>

      {error ? (
        <section
          role="alert"
          className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-border-error-default bg-background-primary-default p-5"
        >
          <div className="flex items-start gap-3">
            <RiErrorWarningLine className="size-5 shrink-0 text-foreground-icon-error" aria-hidden />
            <div className="flex flex-col gap-1">
              <h2 className="text-headline-medium text-text-primary">Could not load spaces</h2>
              <p className="text-body-regular text-text-error-primary">{error}</p>
              <p className="text-body-2-regular text-text-secondary">
                Run migrations and start Django on{" "}
                <span dir="ltr" className="font-mono">
                  http://127.0.0.1:8000
                </span>
                , then seed demo data if the database is empty.
              </p>
            </div>
          </div>
          <Button variant="primary" size="small" onClick={() => void load()}>
            Retry
          </Button>
        </section>
      ) : null}

      {loading && spaces.length === 0 ? (
        <div aria-busy className="grid gap-4 sm:grid-cols-2">
          {Array.from({ length: 2 }, (_, idx) => (
            <div
              key={idx}
              className="flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5"
            >
              <Skeleton shape="rect" className="size-10" />
              <SkeletonText lines={3} />
            </div>
          ))}
        </div>
      ) : null}

      {!loading && !error && spaces.length === 0 ? (
        <section className="flex flex-col gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-6">
          <h2 className="text-title-2-medium text-text-primary">No spaces yet</h2>
          <p className="text-body-regular text-text-secondary">
            Seed the demo floor plan from the repository root after migrating.
          </p>
          <pre
            dir="ltr"
            className="overflow-x-auto rounded-2xl border border-separator-border bg-background-secondary-default p-4 font-mono text-body-2-regular text-text-primary"
          >
            cd backend{"\n"}../.venv/bin/python manage.py seed_demo_data
          </pre>
        </section>
      ) : null}

      {spaces.length > 0 ? (
        <ul className="grid list-none gap-4 p-0 sm:grid-cols-2">
          {spaces.map((space) => {
            const objectCount = space.object_count ?? 0;
            return (
              <li key={space.id}>
                <article
                  className={cx(
                    "flex h-full flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5",
                    "transition-colors hover:border-border-button-hover",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span
                      className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-background-secondary-default text-foreground-icon-primary"
                      aria-hidden
                    >
                      <RiLayoutGridLine className="size-5" />
                    </span>
                    <Chip variant="caption" color="soft">
                      {SPACE_TYPE_LABEL[space.space_type]}
                    </Chip>
                  </div>
                  <div className="flex min-w-0 flex-col gap-1">
                    <h2 className="text-title-3-semibold text-text-primary">{space.label}</h2>
                    <p className="text-body-2-regular text-text-secondary">
                      {formatObjectCount(objectCount)} · Grid step{" "}
                      <bdi dir="ltr">{space.editing_grid_increment_inches}</bdi> in
                    </p>
                  </div>
                  <div className="mt-auto pt-1">
                    <ButtonLink
                      variant="primary"
                      className="w-full sm:w-auto"
                      trailingIcon={RiArrowRightLine}
                      href={editorHref(space.id)}
                    >
                      Open in editor
                    </ButtonLink>
                  </div>
                </article>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}