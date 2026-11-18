import { useEffect, useState } from "react";
import { RiPinterestLine, RiShapesLine } from "@remixicon/react";
import { Counter } from "@/components/base/badges/counter";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { PillTab, PillTabList } from "@/components/base/tabs/pill-tab";
import { ImportVibesModal } from "@/features/home/import-vibes-modal";
import { SectionTitle } from "@/features/home/section-title";
import { useInspirations } from "@/features/home/use-inspirations";
import { OBJECT_TYPE_VISUALS } from "@/features/objects/object-type-visuals";
import { boardDisplayName } from "@/features/spaces/board-url";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { VibeImportStatus } from "@/features/spaces/vibe-import-status";
import type { ApiInspiration } from "@/lib/api";
import { OBJECT_TYPE_OPTIONS } from "@/mocks/types";
import { cx } from "@/utils/cx";

type Group = ApiInspiration["primaryType"];
type Filter = Group | "all";

export const GROUP_ORDER: Group[] = ["furniture", "lighting", "decor", "wall-art", "rug", "storage", "other"];
const TRUSTED_IMAGE_PREFIX = "https://i.pinimg.com/";

export const groupLabel = (group: Group) =>
  group === "other" ? "Other" : (OBJECT_TYPE_OPTIONS.find((o) => o.value === group)?.label ?? group);

export function GroupIcon({ group, className }: { group: Group; className?: string }) {
  if (group === "other") return <RiShapesLine className={cx("text-foreground-icon-tertiary", className)} aria-hidden />;
  const { icon: Icon, iconColor } = OBJECT_TYPE_VISUALS[group];
  return <Icon className={cx(iconColor, className)} aria-hidden />;
}

export function PinCard({ pin }: { pin: ApiInspiration }) {
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = pin.imageUrl.startsWith(TRUSTED_IMAGE_PREFIX) && !imageFailed;
  const label = groupLabel(pin.primaryType);
  const name = pin.item ? pin.item.charAt(0).toUpperCase() + pin.item.slice(1) : label;

  return (
    <div
      title={pin.description || undefined}
      className="flex flex-col gap-2 rounded-3xl border border-border-button-default bg-background-primary-default p-2"
    >
      <div className="aspect-[3/4] overflow-hidden rounded-2xl bg-background-secondary-default">
        {showImage ? (
          <img
            src={pin.imageUrl}
            alt={pin.description || pin.item || "Pinterest pin"}
            className="size-full object-cover"
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="flex size-full items-center justify-center">
            <GroupIcon group={pin.primaryType} className="size-8" />
          </div>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-1 px-1 pb-1">
        <p className="truncate text-body-medium text-text-primary">{name}</p>
        <div className="flex items-center gap-1">
          <GroupIcon group={pin.primaryType} className="size-4 shrink-0" />
          <span className="truncate text-caption-1-medium text-text-secondary">{label}</span>
        </div>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="flex flex-col gap-2 rounded-3xl border border-border-button-default bg-background-primary-default p-2">
      <Skeleton className="aspect-[3/4] w-full rounded-2xl" />
      <div className="flex flex-col gap-2 px-1 pb-1">
        <Skeleton shape="text" className="h-4 w-3/4" />
        <Skeleton shape="text" className="h-3 w-1/2" />
      </div>
    </div>
  );
}

export function VibesInspirationSection() {
  const { status, boards, retry } = useInspirations();
  const vibes = useVibeImport();
  const { refresh: refreshVibes } = vibes;
  const [selected, setSelected] = useState<Filter>("all");
  const [importOpen, setImportOpen] = useState(false);

  useEffect(() => refreshVibes(), [refreshVibes]);

  const importing = vibes.isActive(null);

  const allPins = boards.flatMap((board) => board.inspirations);
  const counts = new Map<Group, number>();
  for (const pin of allPins) counts.set(pin.primaryType, (counts.get(pin.primaryType) ?? 0) + 1);
  const groups = GROUP_ORDER.filter((g) => counts.has(g));
  const filter: Filter = selected !== "all" && !counts.has(selected) ? "all" : selected;

  const title = <SectionTitle id="home-vibes" title="Vibes Inspiration" count={allPins.length} />;
  const headerRow = (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">{title}</div>
      <div className="ms-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
        <VibeImportStatus />
        <Button
          variant="secondary"
          leadingIcon={RiPinterestLine}
          disabled={importing}
          onClick={() => setImportOpen(true)}
        >
          {importing ? "Importing…" : "Import Vibes Board"}
        </Button>
      </div>
    </div>
  );
  const modal = <ImportVibesModal isOpen={importOpen} onOpenChange={setImportOpen} />;

  if (status === "loading") {
    return (
      <>
        <section className="flex flex-col gap-4" aria-labelledby="home-vibes" aria-busy="true">
          {headerRow}
          <div className="flex gap-4 overflow-hidden">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="w-48 shrink-0">
                <SkeletonCard />
              </div>
            ))}
          </div>
        </section>
        {modal}
      </>
    );
  }

  if (status === "error") {
    return (
      <>
        <section className="flex flex-col gap-4" aria-labelledby="home-vibes">
          {headerRow}
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body-regular text-text-secondary">Couldn&apos;t load your pins.</p>
            <Button variant="ghost" size="small" onClick={retry}>
              Try again
            </Button>
          </div>
        </section>
        {modal}
      </>
    );
  }

  if (boards.length === 0 || allPins.length === 0) {
    return (
      <>
        <section className="flex flex-col gap-4" aria-labelledby="home-vibes">
          {headerRow}
          <div className="rounded-3xl border border-dashed border-border-button-default p-8 text-center text-body-regular text-text-secondary">
            Import a Pinterest board to see its pins here, organized by board — or choose one from a space&apos;s
            settings.
          </div>
        </section>
        {modal}
      </>
    );
  }

  const visibleBoards = boards
    .map((board) => {
      const pins = (filter === "all" ? GROUP_ORDER : [filter]).flatMap((g) =>
        board.inspirations.filter((pin) => pin.primaryType === g),
      );
      return { board, pins, name: boardDisplayName(board) };
    })
    .filter((entry) => entry.pins.length > 0);

  return (
    <>
      <section className="flex flex-col gap-6" aria-labelledby="home-vibes">
        <div className="flex min-w-0 flex-col gap-3">
          {headerRow}
          <div className="max-w-full overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <PillTabList aria-label="Filter by type">
              <PillTab variant="gray" isSelected={filter === "all"} onSelect={() => setSelected("all")}>
                All <span className="text-text-tertiary">{allPins.length}</span>
              </PillTab>
              {groups.map((g) => (
                <PillTab key={g} variant="gray" isSelected={filter === g} onSelect={() => setSelected(g)}>
                  {groupLabel(g)} <span className="text-text-tertiary">{counts.get(g)}</span>
                </PillTab>
              ))}
            </PillTabList>
          </div>
        </div>

        <div className="flex flex-col gap-6">
          {visibleBoards.map(({ board, pins, name }) => (
            <Carousel
              key={board.id}
              aria-label={`${name} inspiration`}
              showDots={false}
              gap={16}
              scrollResetKey={filter}
              header={
                <div className="flex min-w-0 items-center gap-2">
                  <RiPinterestLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
                  <h3 className="truncate text-headline-medium text-text-primary">{name}</h3>
                  <Counter>{pins.length}</Counter>
                </div>
              }
            >
              {pins.map((pin) => (
                <CarouselItem key={pin.id} className="w-48">
                  <PinCard pin={pin} />
                </CarouselItem>
              ))}
            </Carousel>
          ))}
        </div>
      </section>
      {modal}
    </>
  );
}
