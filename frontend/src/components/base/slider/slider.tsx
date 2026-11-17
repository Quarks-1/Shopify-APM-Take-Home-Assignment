"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Label as AriaLabel,
  Slider as AriaSlider,
  SliderThumb as AriaSliderThumb,
  SliderTrack as AriaSliderTrack,
} from "react-aria-components";
import type { SliderProps as AriaSliderProps } from "react-aria-components";
import { cx } from "@/utils/cx";

type SliderValue = number | number[];

type SliderBaseProps<T extends SliderValue> = Omit<
  AriaSliderProps<T>,
  "children" | "className" | "orientation"
> & {
  /** Optional visible label associated with every thumb. */
  label?: ReactNode;
  /** Show the exact value in a persistent bubble above each thumb. Default `true`. */
  showTooltip?: boolean;
  /** Custom visual formatter for the value bubbles. */
  formatValue?: (value: number, index: number) => ReactNode;
  /** Per thumb: draw this end as approximate (its fill fades out past the handle). */
  fuzzy?: boolean[];
  /**
   * Per thumb: the span the fade covers, as track fractions 0..1 `[from, to]` in
   * increasing value order. The caller computes it, since scales can be non-linear.
   */
  fuzzySpan?: ([number, number] | undefined)[];
  /**
   * Called when a thumb is pressed and held (450ms, without dragging) or when
   * Enter is pressed on it. Opt-in: a hold ring and an accessible hint appear
   * on each thumb only when this is provided.
   */
  onThumbLongPress?: (index: number) => void;
  className?: string;
};

export type SliderProps = SliderBaseProps<number> & {
  /** Accessible name for the single thumb. */
  thumbLabel?: string;
};

export type RangeSliderProps = SliderBaseProps<number[]> & {
  /** Accessible names for the lower and upper thumbs. */
  thumbLabels?: [string, string];
};

const HOLD_MS = 450;
const HOLD_SLOP_PX = 4;
const FADE_MS = 250;

/** True for `ms` after `key` last changed (not on mount), so a transition can be scoped to that change. */
function useRecentChange(key: string, ms: number): boolean {
  const [recent, setRecent] = useState(false);
  const previous = useRef(key);
  useEffect(() => {
    if (previous.current === key) return;
    previous.current = key;
    setRecent(true);
    const timer = window.setTimeout(() => setRecent(false), ms);
    return () => window.clearTimeout(timer);
  }, [key, ms]);
  return recent;
}

/**
 * Press-and-hold (and Enter) on a thumb. React Aria's SliderThumb does not
 * forward pointer handlers to the DOM, so native listeners go on its node via a
 * ref. They sit beside React Aria's own drag: a stationary hold never changes
 * the value, and moving or releasing cancels the timer. Returns whether a hold
 * is pending (drives the progress ring).
 */
function useThumbHold(
  ref: React.RefObject<HTMLDivElement | null>,
  index: number,
  value: number,
  onLongPress: ((index: number) => void) | undefined,
): boolean {
  const [holding, setHolding] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);
  const latest = useRef(onLongPress);
  useEffect(() => {
    latest.current = onLongPress;
  });
  const enabled = onLongPress !== undefined;

  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled) return;

    const fire = () => {
      latest.current?.(index);
      if (typeof navigator !== "undefined") navigator.vibrate?.(10);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      cancelRef.current?.();
      const startX = event.clientX;
      const startY = event.clientY;
      const stop = () => {
        window.clearTimeout(timer);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", stop);
        window.removeEventListener("pointercancel", stop);
        node.removeEventListener("lostpointercapture", stop);
        cancelRef.current = null;
        setHolding(false);
      };
      const onMove = (e: PointerEvent) => {
        if (Math.hypot(e.clientX - startX, e.clientY - startY) > HOLD_SLOP_PX) stop();
      };
      const timer = window.setTimeout(() => {
        stop();
        fire();
      }, HOLD_MS);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
      node.addEventListener("lostpointercapture", stop);
      cancelRef.current = stop;
      setHolding(true);
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || event.repeat) return;
      event.preventDefault();
      fire();
    };

    node.addEventListener("pointerdown", onPointerDown);
    node.addEventListener("keydown", onKeyDown);
    return () => {
      node.removeEventListener("pointerdown", onPointerDown);
      node.removeEventListener("keydown", onKeyDown);
      cancelRef.current?.();
    };
  }, [ref, index, enabled]);

  // A changed value means it is a drag, not a hold.
  useEffect(() => {
    cancelRef.current?.();
  }, [value]);

  return holding;
}

interface SliderThumbViewProps {
  index: number;
  label: string;
  value: number;
  valueLabel: ReactNode;
  showTooltip: boolean;
  fuzzy: boolean;
  onLongPress?: (index: number) => void;
}

function SliderThumbView({ index, label, value, valueLabel, showTooltip, fuzzy, onLongPress }: SliderThumbViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const holding = useThumbHold(ref, index, value, onLongPress);
  const hasHint = onLongPress !== undefined;

  useEffect(() => {
    const input = ref.current?.querySelector("input");
    if (!input) return;
    if (hasHint) input.setAttribute("aria-describedby", hintId);
    else input.removeAttribute("aria-describedby");
  }, [hasHint, hintId]);

  return (
    <AriaSliderThumb
      ref={ref}
      index={index}
      aria-label={label}
      className={({ isDragging, isHovered: isThumbHovered, isFocusVisible, isDisabled }) =>
        cx(
          "top-1/2 size-5 rounded-full border outline-none",
          fuzzy ? "border-accent-300" : "border-border-button-default",
          "flex items-center justify-center bg-control-indicator-background",
          "shadow-[0_2px_4px_rgb(0_0_0/0.10),inset_0_1px_0_rgb(255_255_255/0.8)]",
          "transition-[scale,box-shadow,border-color] duration-150 ease",
          isThumbHovered && !isDragging && (fuzzy ? "scale-105" : "scale-105 border-border-button-hover"),
          isDragging &&
            "z-20 scale-110 border-accent-500 shadow-[0_3px_8px_color-mix(in_srgb,var(--color-accent-600)_22%,transparent)]",
          isFocusVisible && "ring-2 ring-border-focus-ring ring-offset-2",
          isDisabled ? "cursor-not-allowed" : "cursor-grab active:cursor-grabbing",
        )
      }
    >
      {({ isDragging, isFocused }) => (
        <>
          {showTooltip && (
            <span
              aria-hidden
              className={cx(
                "pointer-events-none absolute bottom-[calc(100%+9px)] left-1/2 -translate-x-1/2",
                "flex h-7 min-w-8 items-center justify-center rounded-lg border border-border-button-default",
                "bg-background-primary-default px-2 text-caption-1-medium whitespace-nowrap text-text-primary shadow-xs",
                "transition-[translate,box-shadow] duration-150 ease",
                (isDragging || isFocused) && "-translate-y-0.5 shadow-dropdown",
              )}
            >
              {valueLabel}
              <span
                aria-hidden
                className="absolute -bottom-[4.5px] left-1/2 size-2 -translate-x-1/2 rotate-45 border-r border-b border-border-button-default bg-background-primary-default"
              />
            </span>
          )}
          <span
            aria-hidden
            className={cx(
              "size-2 rounded-full bg-linear-to-b from-accent-500 to-accent-600",
              "transition-[filter,box-shadow] duration-150 ease",
              fuzzy
                ? "blur-[1.5px] shadow-[0_0_0_4px_color-mix(in_srgb,var(--color-accent-500)_18%,transparent)]"
                : "shadow-[inset_0_1px_0_rgb(255_255_255/0.24)]",
              (isDragging || isFocused) && "brightness-110",
            )}
          />
          {holding && <span aria-hidden className="slider-hold-ring" />}
          {hasHint && (
            <span id={hintId} className="sr-only">
              Press and hold, or press Enter, to make this end approximate
            </span>
          )}
        </>
      )}
    </AriaSliderThumb>
  );
}

type SliderBaseInternalProps<T extends SliderValue> = SliderBaseProps<T> & {
  thumbLabels: string[];
};

/**
 * Shared single and multi-thumb slider presentation.
 *
 * React Aria owns the value model, drag behavior, hidden range inputs,
 * keyboard controls, RTL handling, and collision rules. BoardUI owns the
 * visual track, selected range, thumbs, and exact-value bubbles.
 */
function SliderBase<T extends SliderValue>({
  className,
  label,
  showTooltip = true,
  formatValue,
  thumbLabels,
  fuzzy,
  fuzzySpan,
  onThumbLongPress,
  ...props
}: SliderBaseInternalProps<T>) {
  const fadeAnimating = useRecentChange((fuzzy ?? []).map(Boolean).join(), FADE_MS + 50);
  return (
    <AriaSlider
      {...props}
      orientation="horizontal"
      className={({ isDisabled }) =>
        cx(
          "group flex w-full min-w-0 flex-col gap-2",
          isDisabled && "cursor-not-allowed opacity-50",
          className,
        )
      }
    >
      {({ state }) => {
        const isRange = state.values.length > 1;
        const startPercent = isRange ? state.getThumbPercent(0) : 0;
        const lastIndex = state.values.length - 1;
        const endPercent = state.getThumbPercent(lastIndex);

        // A fuzzy end moves the solid fill's edge to the inner end of its fade span.
        const startSpan = fuzzy?.[0] ? fuzzySpan?.[0] : undefined;
        const endSpan = fuzzy?.[lastIndex] ? fuzzySpan?.[lastIndex] : undefined;
        let solidStart = isRange && startSpan ? startSpan[1] : startPercent;
        let solidEnd = endSpan ? endSpan[0] : endPercent;
        if (solidStart > solidEnd) solidStart = solidEnd = (solidStart + solidEnd) / 2;
        const edgeTransition = fadeAnimating
          ? `inset-inline ${FADE_MS}ms ease, opacity ${FADE_MS}ms ease`
          : undefined;

        const fade = (span: [number, number] | undefined, active: boolean, toward: "start" | "end") =>
          span ? (
            <span
              key={toward}
              aria-hidden
              className={cx(
                "pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2",
                "bg-linear-to-r rtl:bg-linear-to-l",
                toward === "end" ? "from-accent-500 to-transparent" : "from-transparent to-accent-500",
                "transition-opacity duration-250 ease-in-out",
                active ? "opacity-100" : "opacity-0",
              )}
              style={{
                insetInlineStart: `${span[0] * 100}%`,
                insetInlineEnd: `${(1 - span[1]) * 100}%`,
              }}
            />
          ) : null;

        const fillSpans = (
          <>
            <span
              aria-hidden
              className={cx(
                "absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full",
                startSpan && "rounded-s-none",
                endSpan && "rounded-e-none",
                "bg-linear-to-r from-accent-500 to-accent-600",
                "shadow-[inset_0_1px_0_rgb(255_255_255/0.24),0_1px_2px_rgb(4_80_226/0.16)]",
              )}
              style={{
                insetInlineStart: `${solidStart * 100}%`,
                insetInlineEnd: `${(1 - solidEnd) * 100}%`,
                transition: edgeTransition,
              }}
            />
            {isRange && fade(fuzzySpan?.[0], !!startSpan, "start")}
            {fade(fuzzySpan?.[lastIndex], !!endSpan, "end")}
          </>
        );

        return (
          <>
            {label != null && (
              <AriaLabel className="text-body-medium text-text-primary">
                {label}
              </AriaLabel>
            )}

            <AriaSliderTrack
              className={cx(
                "relative h-8 w-full touch-none",
                showTooltip && "mt-8",
              )}
            >
              {({ isHovered }) => (
                <>
                  <span
                    aria-hidden
                    className={cx(
                      "absolute top-1/2 end-0 start-0 h-1.5 -translate-y-1/2 rounded-full",
                      "bg-background-tertiary-default shadow-[inset_0_1px_1px_rgb(0_0_0/0.06)]",
                      "transition-colors duration-150 ease",
                      isHovered && "bg-background-tertiary-hover",
                    )}
                  />
                  {fillSpans}

                  {state.values.map((value, index) => (
                    <SliderThumbView
                      key={index}
                      index={index}
                      label={thumbLabels[index] ?? (isRange ? `Value ${index + 1}` : "Value")}
                      value={value}
                      valueLabel={formatValue?.(value, index) ?? state.getThumbValueLabel(index)}
                      showTooltip={showTooltip}
                      fuzzy={!!fuzzy?.[index] && !!fuzzySpan?.[index]}
                      onLongPress={onThumbLongPress}
                    />
                  ))}
                </>
              )}
            </AriaSliderTrack>
          </>
        );
      }}
    </AriaSlider>
  );
}

/** A single-value slider with an exact-value bubble above the thumb. */
export function Slider({ thumbLabel = "Value", ...props }: SliderProps) {
  return <SliderBase {...props} thumbLabels={[thumbLabel]} />;
}

/** A two-thumb slider for selecting a minimum and maximum value. */
export function RangeSlider({
  thumbLabels = ["Minimum", "Maximum"],
  ...props
}: RangeSliderProps) {
  return <SliderBase {...props} thumbLabels={thumbLabels} />;
}
