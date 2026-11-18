import { useState } from "react";
import { RiShapesLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { PillTab, PillTabList } from "@/components/base/tabs/pill-tab";
import { SectionTitle } from "@/features/home/section-title";
import { useInspirations } from "@/features/home/use-inspirations";
import { OBJECT_TYPE_VISUALS } from "@/features/objects/object-type-visuals";
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

function safeHttpsUrl(raw: string | undefined): string | undefined {
  if (!raw) return undefined;
  try {
    return new URL(raw).protocol === "https:" ? raw : undefined;
  } catch {
    return undefined;
  }
}

export function PinCard({ pin, linkToPin = false }: { pin: ApiInspiration; linkToPin?: boolean }) {
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = pin.imageUrl.startsWith(TRUSTED_IMAGE_PREFIX) && !imageFailed;
  const label = groupLabel(pin.primaryType);
  const name = pin.item ? pin.item.charAt(0).toUpperCase() + pin.item.slice(1) : label;
  const href = linkToPin ? safeHttpsUrl(pin.pinUrl) : undefined;

  const card = (
    <div
      title={pin.description || undefined}
      className={cx(
        "flex flex-col gap-2 rounded-3xl border border-border-button-default bg-background-primary-default p-2",
        href && "transition-colors hover:border-border-button-hover",
      )}
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

  if (!href) return card;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded-3xl outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring"
    >
      {card}
    </a>
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
  const { status, inspirations, retry } = useInspirations();
  const [selected, setSelected] = useState<Filter>("all");

  const counts = new Map<Group, number>();
  for (const pin of inspirations) counts.set(pin.primaryType, (counts.get(pin.primaryType) ?? 0) + 1);
  const groups = GROUP_ORDER.filter((g) => counts.has(g));
  const filter: Filter = selected !== "all" && !counts.has(selected) ? "all" : selected;

  const title = (
    <SectionTitle id="home-vibes" title="Vibes Inspiration" count={inspirations.length} />
  );

  if (status === "loading") {
    return (
      <section className="flex flex-col gap-4" aria-labelledby="home-vibes" aria-busy="true">
        {title}
        <div className="flex gap-4 overflow-hidden">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="w-48 shrink-0">
              <SkeletonCard />
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (status === "error") {
    return (
      <section className="flex flex-col gap-4" aria-labelledby="home-vibes">
        {title}
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body-regular text-text-secondary">Couldn't load your pins.</p>
          <Button variant="ghost" size="small" onClick={retry}>
            Try again
          </Button>
        </div>
      </section>
    );
  }

  if (inspirations.length === 0) {
    return (
      <section className="flex flex-col gap-4" aria-labelledby="home-vibes">
        {title}
        <div className="rounded-3xl border border-dashed border-border-button-default p-8 text-center text-body-regular text-text-secondary">
          Import a Pinterest board from a space's settings and its pins show up here, sorted by type.
        </div>
      </section>
    );
  }

  const visible = (filter === "all" ? groups : [filter]).flatMap((g) =>
    inspirations.filter((pin) => pin.primaryType === g),
  );

  return (
    <section className="flex flex-col gap-4" aria-labelledby="home-vibes">
      <Carousel
        aria-label="Vibes Inspiration"
        showDots={false}
        gap={16}
        scrollResetKey={filter}
        header={
          <div className="flex min-w-0 flex-col gap-3">
            {title}
            <div className="max-w-full overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              <PillTabList aria-label="Filter by type">
                <PillTab variant="gray" isSelected={filter === "all"} onSelect={() => setSelected("all")}>
                  All <span className="text-text-tertiary">{inspirations.length}</span>
                </PillTab>
                {groups.map((g) => (
                  <PillTab key={g} variant="gray" isSelected={filter === g} onSelect={() => setSelected(g)}>
                    {groupLabel(g)} <span className="text-text-tertiary">{counts.get(g)}</span>
                  </PillTab>
                ))}
              </PillTabList>
            </div>
          </div>
        }
      >
        {visible.map((pin) => (
          <CarouselItem key={pin.id} className="w-48">
            <PinCard pin={pin} linkToPin />
          </CarouselItem>
        ))}
      </Carousel>
    </section>
  );
}
