import { useEffect, type ReactNode } from "react";
import { ScrollFade, useScrollFade } from "@/components/foundations/scroll-fade/scroll-fade";
import { cx } from "@/utils/cx";
import { ObjectCarouselRow } from "./object-carousel-row";

export interface ObjectCarouselListProps {
  objectIds: string[];
  spaceId?: string;
  highlightedId?: string | null;
  onHighlight?: (objectId: string) => void;
  emptyState?: ReactNode;
  /** Make the list its own vertical scroll region (needs a bounded-height parent). */
  scrollable?: boolean;
  /** Surface behind the scroll region; used by {@link ScrollFade}. */
  scrollFadeSurfaceClassName?: string;
  className?: string;
}

export function ObjectCarouselList({
  objectIds,
  spaceId,
  highlightedId,
  onHighlight,
  emptyState,
  scrollable = false,
  scrollFadeSurfaceClassName = "from-background-primary-default",
  className,
}: ObjectCarouselListProps) {
  const fade = useScrollFade();

  useEffect(() => {
    if (!highlightedId) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById(`object-row-${highlightedId}`)
      ?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [highlightedId]);

  if (objectIds.length === 0) return <>{emptyState}</>;

  const rows = (
    <div className={cx("flex flex-col gap-4", !scrollable && className)}>
      {objectIds.map((id) => (
        <ObjectCarouselRow
          key={id}
          objectId={id}
          spaceId={spaceId}
          highlighted={id === highlightedId}
          onFocusRow={onHighlight}
        />
      ))}
    </div>
  );

  if (!scrollable) return rows;

  return (
    <div className={cx("relative min-h-0 flex-1", className)}>
      <div onScroll={fade.onScroll} className="h-full min-h-0 overflow-y-auto overscroll-contain p-1">
        {rows}
      </div>
      <ScrollFade progress={fade.progress} surfaceClassName={scrollFadeSurfaceClassName} />
    </div>
  );
}
