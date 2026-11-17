import { useId, useLayoutEffect, useRef } from "react";
import { RangeSlider } from "@/components/base/slider/slider";
import {
  MAX_STOP_INDEX,
  MIN_STOP_INDEX,
  priceToStopIndex,
  stopIndexToPrice,
} from "@/lib/price-scale";
import { cx } from "@/utils/cx";

export interface PriceRange {
  /** Undefined means no minimum ($0 end of the slider). */
  min?: number;
  /** Undefined means no maximum ($10,000+ end of the slider). */
  max?: number;
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

/** Spoken value of one thumb: "$250", or "$10,000+" at the open upper end. */
function thumbText(index: number): string {
  const price = money(stopIndexToPrice(index));
  return index === MAX_STOP_INDEX ? `${price}+` : price;
}

/** "$100 – $1,500", "Up to $1,500", "$100+" or "Any price". */
function RangeText({ value }: { value: PriceRange }) {
  const { min, max } = value;
  if (min !== undefined && max !== undefined) {
    return (
      <>
        <bdi>{money(min)}</bdi> – <bdi>{money(max)}</bdi>
      </>
    );
  }
  if (max !== undefined) {
    return (
      <>
        Up to <bdi>{money(max)}</bdi>
      </>
    );
  }
  if (min !== undefined) {
    return (
      <>
        <bdi>{money(min)}</bdi>+
      </>
    );
  }
  return <>Any price</>;
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
  useLayoutEffect(() => {
    const inputs = rootRef.current?.querySelectorAll<HTMLInputElement>('input[type="range"]');
    inputs?.[0]?.setAttribute("aria-valuetext", thumbText(minIndex));
    inputs?.[1]?.setAttribute("aria-valuetext", thumbText(maxIndex));
  });

  const handleChange = (indexes: number[]) => {
    const [nextMin, nextMax] = indexes;
    onChange({
      min: nextMin === minIndex ? value.min : nextMin === MIN_STOP_INDEX ? undefined : stopIndexToPrice(nextMin),
      max: nextMax === maxIndex ? value.max : nextMax === MAX_STOP_INDEX ? undefined : stopIndexToPrice(nextMax),
    });
  };

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
      />
    </div>
  );
}
