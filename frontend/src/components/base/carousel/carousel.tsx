"use client";

import {
  Children,
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type ReactNode,
  type Ref,
} from "react";
import { RiArrowLeftSLine, RiArrowRightSLine } from "@remixicon/react";
import { IconButton } from "@/components/base/buttons/icon-button";
import { useDirection } from "@/components/foundations/direction/direction";
import { inlineScrollOffset, scrollOffsetForItem } from "@/utils/scroll-direction";
import { cx } from "@/utils/cx";

/**
 * Gallery carousel: a horizontal run of cards you slide through.
 *
 * Built on native scrolling with CSS scroll-snap rather than a transformed
 * track. That is the whole design decision, and it buys a lot: touch swipe,
 * trackpad gestures, momentum, and keyboard scrolling all work without a line
 * of JavaScript, and the browser handles sub-pixel positioning better than a
 * translate ever does. JS is only here to answer three questions the DOM will
 * not answer on its own — which card is showing, and whether either end has
 * been reached.
 *
 * Positions are read from each item's viewport bounds instead of being computed
 * from a card width, so items of different widths, responsive widths, or a
 * changed gap all keep working without the maths going stale.
 *
 * Follows the APG carousel pattern for naming: the region is a labelled group
 * that announces itself as a carousel, each item announces its position, and
 * the controls are real buttons that disable at the ends.
 */

export interface CarouselProps
  extends Omit<HTMLAttributes<HTMLDivElement>, "children" | "onScroll"> {
  children: ReactNode;
  /** Names the carousel for assistive tech. */
  "aria-label": string;
  /** Prev/next buttons above the track. */
  showArrows?: boolean;
  /** Content at the start of the controls row (e.g. a section title). */
  header?: ReactNode;
  /** Content at the end of the controls row, just before the arrows. */
  actions?: ReactNode;
  /** Position indicator below the track. */
  showDots?: boolean;
  /** Where a card comes to rest when it snaps. */
  align?: "start" | "center";
  /** Gap between cards, in px. */
  gap?: number;
  /**
   * When this value changes after mount, the track scrolls back to the first
   * card (smoothly, or instantly under reduced motion). Use it when the items
   * are reordered and the new first item should be brought into view.
   */
  scrollResetKey?: unknown;
  ref?: Ref<HTMLDivElement>;
}

export interface CarouselItemProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  ref?: Ref<HTMLDivElement>;
}

/**
 * One card, filling the track so exactly one is in view per slide. Width lives
 * here rather than on the track, so `className` can override it for a gallery
 * that shows a peek of the next card.
 */
export function CarouselItem({ children, className, ref, ...props }: CarouselItemProps) {
  return (
    <div
      ref={ref}
      role="group"
      aria-roledescription="slide"
      className={cx("w-full shrink-0 snap-start", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export function Carousel({
  children,
  showArrows = true,
  header,
  actions,
  showDots = true,
  align = "start",
  gap = 16,
  scrollResetKey,
  className,
  ref,
  ...props
}: CarouselProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const direction = useDirection();
  const [active, setActive] = useState(0);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(false);

  const count = Children.toArray(children).filter(isValidElement).length;

  const items = useCallback(
    () => Array.from(trackRef.current?.children ?? []) as HTMLElement[],
    [],
  );

  // Skip the first run: only a *change* of the key scrolls back to the start.
  const lastResetKey = useRef(scrollResetKey);
  useEffect(() => {
    if (Object.is(lastResetKey.current, scrollResetKey)) return;
    lastResetKey.current = scrollResetKey;
    const track = trackRef.current;
    if (!track) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // 0 is the inline start in both LTR and RTL in current browsers.
    track.scrollTo({ left: 0, behavior: reduce ? "auto" : "smooth" });
  }, [scrollResetKey]);

  const measure = useCallback(() => {
    const track = trackRef.current;
    if (!track) return;

    // 1px of slack: scrollLeft is fractional on hi-DPI displays, so an exact
    // comparison leaves the end arrow enabled on a fully scrolled track.
    const offset = inlineScrollOffset(track.scrollLeft, direction);
    const start = offset <= 1;
    const end = offset >= track.scrollWidth - track.clientWidth - 1;
    setAtStart(start);
    setAtEnd(end);

    const all = items();
    if (all.length === 0) return;

    // At the far end the last cards share the viewport, so the final card can
    // never reach the start edge and "nearest" would settle on the one before
    // it — leaving the last dot permanently unlit and making a click on it
    // look broken. The ends are therefore pinned rather than measured.
    if (end) {
      setActive(all.length - 1);
      return;
    }
    if (start) {
      setActive(0);
      return;
    }

    let nearest = 0;
    let shortest = Number.POSITIVE_INFINITY;
    all.forEach((item, index) => {
      const distance = Math.abs(scrollOffsetForItem(track.getBoundingClientRect(), item.getBoundingClientRect(), 0, direction, align));
      if (distance < shortest) {
        shortest = distance;
        nearest = index;
      }
    });
    setActive(nearest);
  }, [items, direction, align]);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    // ResizeObserver fires once on observe, which is what takes the first
    // measurement — no synchronous setState in the effect body.
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    track.addEventListener("scroll", measure, { passive: true });
    return () => {
      observer.disconnect();
      track.removeEventListener("scroll", measure);
    };
  }, [measure]);

  const scrollToIndex = (index: number) => {
    const track = trackRef.current;
    const target = items()[Math.max(0, Math.min(index, count - 1))];
    if (!track || !target) return;
    // `matchMedia` rather than a hook: this runs on click, so reading the
    // preference at that moment is both current and cheap.
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const left = scrollOffsetForItem(track.getBoundingClientRect(), target.getBoundingClientRect(), track.scrollLeft, direction, align);
    track.scrollTo({ left, behavior: reduce ? "auto" : "smooth" });
  };

  return (
    <div
      ref={ref}
      role="group"
      dir={direction}
      aria-roledescription="carousel"
      className={cx("flex w-full flex-col gap-4", className)}
      {...props}
    >
      {header || actions || (showArrows && count > 0) ? (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          {header ? <div className="min-w-0">{header}</div> : null}
          <div className="ms-auto flex items-center gap-2">
            {actions}
            {showArrows && count > 0 ? (
              <>
                <IconButton
                  icon={RiArrowLeftSLine}
                  size="small"
                  aria-label="Previous slide"
                  disabled={atStart}
                  onClick={() => scrollToIndex(active - 1)}
                />
                <IconButton
                  icon={RiArrowRightSLine}
                  size="small"
                  aria-label="Next slide"
                  disabled={atEnd}
                  onClick={() => scrollToIndex(active + 1)}
                />
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* `tabIndex` makes the track focusable so it can be scrolled with the
          arrow keys, which is the browser's own behaviour once it has focus.
          The scrollbar is hidden inline rather than through a global class, so
          the component stays self-contained. The track is padded and pulled back
          by the same amount with negative margin so card rings and focus rings
          aren't clipped by the scroll container. */}
      <div
        ref={trackRef}
        tabIndex={0}
        className={cx(
          "relative -m-1 flex overflow-x-auto overscroll-x-contain p-1 scroll-px-1 outline-none",
          "snap-x snap-mandatory [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
          "focus-visible:ring-2 focus-visible:ring-border-focus-ring focus-visible:ring-offset-2",
          align === "center" && "[&>*]:snap-center",
        )}
        style={{ gap }}
      >
        {/* The position label is applied here so callers never have to
            hand-number their own slides. */}
        {Children.map(children, (child, index) =>
          isValidElement<CarouselItemProps>(child)
            ? cloneElement(child, { "aria-label": `${index + 1} of ${count}` })
            : child,
        )}
      </div>

      {showDots && count > 1 ? (
        <div className="flex items-center justify-center gap-1.5">
          {Array.from({ length: count }, (_, index) => (
            <button
              key={index}
              type="button"
              aria-label={`Go to slide ${index + 1}`}
              aria-current={index === active}
              onClick={() => scrollToIndex(index)}
              className={cx(
                "h-1.5 cursor-pointer rounded-full transition-all duration-200 ease-out",
                index === active
                  ? "w-4 bg-text-primary"
                  : "w-1.5 bg-background-tertiary-default hover:bg-border-button-active",
              )}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
