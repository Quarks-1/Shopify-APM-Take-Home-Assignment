import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Select, SelectItem } from "@/components/base/select/select";
import { Spinner } from "@/components/base/spinner/spinner";
import { formatInches, gridCellCount } from "@/lib/editing-grid";
import {
  fetchSpaceObjects,
  fetchSpaces,
  type PlacedObject,
  type SpaceObjectsPayload,
  type SpaceSummary,
} from "@/lib/spaces";
import { cx } from "@/utils/cx";
import { ObjectAttributeMenu } from "./object-attribute-menu";

const TYPE_LABELS: Record<PlacedObject["object_type"], string> = {
  furniture: "Furniture",
  poster: "Poster",
};

interface MenuTarget {
  object: PlacedObject;
  point: { x: number; y: number };
}

function percentAlong(value: string, span: string): number {
  const size = Number(span);
  const position = Number(value);
  if (!Number.isFinite(size) || size <= 0 || !Number.isFinite(position)) return 0;
  return Math.min(100, Math.max(0, (position / size) * 100));
}

export function SpaceEditor() {
  const [spaces, setSpaces] = useState<SpaceSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SpaceObjectsPayload | null>(null);
  const [loadingSpaces, setLoadingSpaces] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<MenuTarget | null>(null);

  const loadSpaces = useCallback(async (signal?: AbortSignal) => {
    setLoadingSpaces(true);
    setError(null);
    try {
      const payload = await fetchSpaces(signal);
      if (signal?.aborted) return;
      setSpaces(payload.spaces);
      setSelectedId((current) => {
        if (current != null && payload.spaces.some((space) => space.id === current)) return current;
        return payload.spaces[0]?.id ?? null;
      });
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : "Unable to load spaces");
    } finally {
      if (!signal?.aborted) setLoadingSpaces(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadSpaces(controller.signal);
    return () => controller.abort();
  }, [loadSpaces]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setLoadingDetail(true);
    setError(null);
    fetchSpaceObjects(selectedId, controller.signal)
      .then((payload) => {
        setDetail(payload);
        setMenu(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Unable to load objects");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingDetail(false);
      });
    return () => controller.abort();
  }, [selectedId]);

  const openMenu = (object: PlacedObject, x: number, y: number) => {
    setMenu({ object, point: { x, y } });
  };

  const onUpdated = (next: PlacedObject) => {
    setDetail((current) => {
      if (!current) return current;
      const stillHere = next.space_ids.includes(current.space.id);
      const exists = current.objects.some((item) => item.id === next.id);
      let objects = current.objects;
      if (stillHere) {
        objects = exists
          ? current.objects.map((item) => (item.id === next.id ? next : item))
          : [...current.objects, next];
      } else {
        objects = current.objects.filter((item) => item.id !== next.id);
      }
      return { ...current, objects };
    });
    setMenu((current) => (current && current.object.id === next.id ? { ...current, object: next } : current));
  };

  const space = detail?.space;
  const cellsX = space
    ? gridCellCount(space.x, space.editing_grid_increment_inches, space.dimension_unit)
    : 1;
  const cellsY = space
    ? gridCellCount(space.y, space.editing_grid_increment_inches, space.dimension_unit)
    : 1;
  const spanX = space && Number(space.x) > 0 ? Number(space.x) : 1;
  const spanY = space && Number(space.y) > 0 ? Number(space.y) : 1;

  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-title-1-medium text-text-primary">Space editor</h1>
        <p className="text-body-regular text-text-secondary">
          Right-click an object to change its attributes. The floor plan shows this space&apos;s editing grid.
        </p>
      </header>

      {error ? (
        <section
          role="alert"
          className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-border-error-default bg-background-primary-default p-5"
        >
          <p className="text-body-regular text-text-error-primary">{error}</p>
          <Button variant="primary" size="small" onClick={() => void loadSpaces()}>
            Retry
          </Button>
        </section>
      ) : null}

      <section className="flex flex-col gap-4 rounded-3xl border border-border-button-default bg-background-primary-default p-5 sm:p-6">
        {loadingSpaces ? (
          <div className="flex items-center gap-2 text-body-regular text-text-secondary">
            <Spinner size="sm" aria-hidden />
            <span>Loading spaces</span>
          </div>
        ) : spaces.length === 0 ? (
          error ? null : (
          <div className="flex flex-col gap-1">
            <h2 className="text-title-3-semibold text-text-primary">No spaces yet</h2>
            <p className="text-body-regular text-text-secondary">
              Add a space and an object in Django admin, then reload this editor.
            </p>
          </div>
          )
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="flex min-w-0 flex-col gap-1">
                <h2 className="text-title-2-medium text-text-primary">{space?.label ?? "Space"}</h2>
                {space ? (
                  <p className="text-caption-1-medium text-text-tertiary">
                    Editing grid {formatInches(space.editing_grid_increment_inches)}
                  </p>
                ) : null}
              </div>
              {spaces.length > 1 ? (
                <Select
                  aria-label="Space"
                  size="sm"
                  className="w-full sm:w-64"
                  selectedKey={selectedId == null ? null : String(selectedId)}
                  onSelectionChange={(key) => {
                    if (key != null) setSelectedId(Number(key));
                  }}
                >
                  {spaces.map((option) => (
                    <SelectItem key={option.id} id={String(option.id)} textValue={option.label}>
                      {option.label}
                    </SelectItem>
                  ))}
                </Select>
              ) : null}
            </div>

            {loadingDetail || !space ? (
              <div className="flex items-center gap-2 text-body-regular text-text-secondary">
                <Spinner size="sm" aria-hidden />
                <span>Loading objects</span>
              </div>
            ) : (
              <div
                className="relative mx-auto w-full overflow-hidden rounded-2xl border border-separator-border bg-background-secondary-default"
                style={{
                  aspectRatio: `${spanX} / ${spanY}`,
                  width: `min(100%, calc(24rem * ${spanX} / ${spanY}))`,
                }}
                data-editing-grid-inches={space.editing_grid_increment_inches}
                data-dimension-unit={space.dimension_unit}
              >
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-0"
                  style={{
                    backgroundImage: [
                      "linear-gradient(to right, var(--color-separator-border) 1px, transparent 1px)",
                      "linear-gradient(to bottom, var(--color-separator-border) 1px, transparent 1px)",
                    ].join(", "),
                    backgroundSize: `${100 / cellsX}% ${100 / cellsY}%`,
                  }}
                />
                <div role="group" aria-label="Objects in this space" className="absolute inset-0">
                  {detail.objects.length === 0 ? (
                    <p className="p-4 text-body-regular text-text-secondary">This space has no objects yet.</p>
                  ) : (
                    detail.objects.map((object) => {
                      const label = `${TYPE_LABELS[object.object_type]} ${object.id}`;
                      const active = menu?.object.id === object.id;
                      return (
                        <button
                          key={object.id}
                          type="button"
                          aria-haspopup="dialog"
                          aria-label={`Edit attributes for ${label}`}
                          data-object-id={object.id}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            openMenu(object, event.clientX, event.clientY);
                          }}
                          onKeyDown={(event) => {
                            if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                            event.preventDefault();
                            const rect = event.currentTarget.getBoundingClientRect();
                            openMenu(object, rect.left, rect.bottom);
                          }}
                          className={cx(
                            "absolute z-10 max-w-32 -translate-x-1/2 -translate-y-1/2 rounded-full border border-border-button-default bg-background-primary-default px-2 py-1 text-caption-1-semibold text-text-primary shadow-xs",
                            "cursor-pointer outline-none hover:bg-background-primary-hover focus-visible:ring-2 focus-visible:ring-border-focus-ring",
                            active && "ring-2 ring-border-focus-ring",
                          )}
                          style={{
                            left: `${percentAlong(object.x, space.x)}%`,
                            top: `${percentAlong(object.y, space.y)}%`,
                          }}
                        >
                          {label}
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </section>

      {menu && space ? (
        <ObjectAttributeMenu
          key={menu.object.id}
          object={menu.object}
          space={space}
          spaces={detail?.spaces ?? spaces}
          point={menu.point}
          onClose={() => setMenu(null)}
          onUpdated={onUpdated}
        />
      ) : null}
    </>
  );
}
