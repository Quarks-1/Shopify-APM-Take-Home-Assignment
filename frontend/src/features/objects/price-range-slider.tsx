import { useId, useLayoutEffect, useRef } from "react";
import { RangeSlider } from "@/components/base/slider/slider";
import {
  MAX_STOP_INDEX,
  MIN_STOP_INDEX,
  PRICE_FUZZ,
  priceToFractionalIndex,
  priceToStopIndex,
  stopIndexToPrice,
} from "@/lib/price-scale";
import { cx } from "@/utils/cx";

export interface PriceRange {
  /** Undefined means no minimum ($0 end of the slider). */
  min?: number;
  /** Undefined means no maximum ($10,000+ end of the slider). */
  max?: number;
  /** The minimum is approximate ("around"). Ignored while there is no minimum. */
  minFuzzy?: boolean;
  /** The maximum is approximate ("around"). Ignored while there is no maximum. */
  maxFuzzy?: boolean;
}

export interface PriceRangeSliderProps {
  value: PriceRange;
  onChange: (value: PriceRange) => void;
  className?: string;
}

const WHOLE_DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const money = (amount: number) => WHOLE_DOLLARS.format(amount);

/** Spoken value of one thumb: "$250", "around $250", or "$10,000+" at the open upper end. */
function thumbText(index: number, fuzzy: boolean): string {
  const price = money(stopIndexToPrice(index));
  if (index === MAX_STOP_INDEX) return `${price}+`;
  return fuzzy ? `around ${price}` : price;
}

function Around({ children }: { children: string }) {
  return <span className="text-accent-600">{children}</span>;
}

/** "$100 – around $200", "Up to around $200", "Around $100+" or "Any price". */
function RangeText({ value }: { value: PriceRange }) {
  const { min, max } = value;
  const minFuzzy = min !== undefined && !!value.minFuzzy;
  const maxFuzzy = max !== undefined && !!value.maxFuzzy;
  if (min !== undefined && max !== undefined) {
    return (
      <>
        {minFuzzy && <Around>around</Around>} <bdi>{money(min)}</bdi> – {maxFuzzy && <Around>around</Around>}{" "}
        <bdi>{money(max)}</bdi>
      </>
    );
  }
  if (max !== undefined) {
    return (
      <>
        Up to {maxFuzzy && <Around>around</Around>} <bdi>{money(max)}</bdi>
      </>
    );
  }
  if (min !== undefined) {
    return (
      <>
        {minFuzzy && <Around>Around</Around>} <bdi>{money(min)}</bdi>+
      </>
    );
  }
  return <>Any price</>;
}

/** Minimum visual reach of a fade past its handle, as a track fraction (the ±15% band is often narrower than the thumb). */
const MIN_FADE_REACH = 0.08;

/**
 * Fade span `[from, to]` (track fractions, increasing) of an approximate end at `price`.
 * Inward it covers the true -15%/+15%; outward (past the handle, away from the range)
 * it reaches at least MIN_FADE_REACH so the fade shows beyond the thumb.
 */
function fuzzySpanFor(price: number, end: "min" | "max"): [number, number] {
  const clamp = (f: number) => Math.min(1, Math.max(0, f));
  const frac = (p: number) => clamp(priceToFractionalIndex(p) / MAX_STOP_INDEX);
  const at = frac(price);
  const lower = frac(price * (1 - PRICE_FUZZ));
  const upper = frac(price * (1 + PRICE_FUZZ));
  return end === "max"
    ? [lower, Math.max(upper, clamp(at + MIN_FADE_REACH))]
    : [Math.min(lower, clamp(at - MIN_FADE_REACH)), upper];
}

/**
 * Two-thumb slider for a price range. The thumbs move over the stops of
 * `@/lib/price-scale`: the left end ($0) means no minimum and the right end
 * ($10,000+) no maximum. A stored value that sits between stops is drawn at
 * the nearest one and is only rewritten when its own thumb is moved.
 */
export function PriceRangeSlider({ value, onChange, className }: PriceRangeSliderProps) {
  const labelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);

  const minIndex = value.min === undefined ? MIN_STOP_INDEX : priceToStopIndex(value.min);
  const maxIndex = value.max === undefined ? MAX_STOP_INDEX : priceToStopIndex(value.max);

  // The slider's values are stop indexes, which a screen reader would read as "3".
  // React Aria derives aria-valuetext itself, so overwrite it with the price after each render.
  const minFuzzy = value.min !== undefined && !!value.minFuzzy;
  const maxFuzzy = value.max !== undefined && !!value.maxFuzzy;

  useLayoutEffect(() => {
    const inputs = rootRef.current?.querySelectorAll<HTMLInputElement>('input[type="range"]');
    inputs?.[0]?.setAttribute("aria-valuetext", thumbText(minIndex, minFuzzy));
    inputs?.[1]?.setAttribute("aria-valuetext", thumbText(maxIndex, maxFuzzy));
  });

  const handleChange = (indexes: number[]) => {
    const [nextMin, nextMax] = indexes;
    const min = nextMin === minIndex ? value.min : nextMin === MIN_STOP_INDEX ? undefined : stopIndexToPrice(nextMin);
    const max = nextMax === maxIndex ? value.max : nextMax === MAX_STOP_INDEX ? undefined : stopIndexToPrice(nextMax);
    onChange({
      min,
      max,
      minFuzzy: min === undefined ? undefined : value.minFuzzy,
      maxFuzzy: max === undefined ? undefined : value.maxFuzzy,
    });
  };

  const handleLongPress = (index: number) => {
    if (index === 0) {
      if (value.min === undefined) return;
      onChange({ ...value, minFuzzy: !value.minFuzzy });
    } else {
      if (value.max === undefined) return;
      onChange({ ...value, maxFuzzy: !value.maxFuzzy });
    }
  };

  const fuzzySpan: ([number, number] | undefined)[] = [
    value.min === undefined ? undefined : fuzzySpanFor(value.min, "min"),
    value.max === undefined ? undefined : fuzzySpanFor(value.max, "max"),
  ];

  return (
    <div ref={rootRef} className={cx("flex w-full min-w-0 flex-col gap-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span id={labelId} className="text-body-medium text-text-primary">
          Price range
        </span>
        <span className="text-body-regular text-text-secondary">
          <RangeText value={value} />
        </span>
      </div>
      <RangeSlider
        aria-labelledby={labelId}
        thumbLabels={["Minimum price", "Maximum price"]}
        minValue={MIN_STOP_INDEX}
        maxValue={MAX_STOP_INDEX}
        step={1}
        value={[minIndex, maxIndex]}
        onChange={handleChange}
        showTooltip={false}
        fuzzy={[minFuzzy, maxFuzzy]}
        fuzzySpan={fuzzySpan}
        onThumbLongPress={handleLongPress}
      />
      <p className="text-caption-1-medium text-text-tertiary">
        {minFuzzy || maxFuzzy ? "Around means ±15%" : "Hold a handle to make it approximate"}
      </p>
    </div>
  );
}
