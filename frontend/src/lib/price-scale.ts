/**
 * Price scale for the price range slider.
 *
 * The slider works on the INDEX of an ordered list of "nice" price stops, so
 * low prices get fine resolution and high prices coarse: $10 steps up to $100,
 * then $25, $50, $100, $250, $500 and finally $1,000 steps up to $10,000.
 */

/** Inclusive upper bound and step size of each band of the scale. */
const BANDS: readonly { to: number; step: number }[] = [
  { to: 100, step: 10 },
  { to: 250, step: 25 },
  { to: 500, step: 50 },
  { to: 1000, step: 100 },
  { to: 2500, step: 250 },
  { to: 5000, step: 500 },
  { to: 10000, step: 1000 },
];

function buildStops(): readonly number[] {
  const stops = [0];
  for (const { to, step } of BANDS) {
    for (let price = stops[stops.length - 1] + step; price <= to; price += step) stops.push(price);
  }
  return stops;
}

/** Every selectable price, ascending: 0, 10, 20 … 100, 125 … 250, 300 … 10,000. */
export const PRICE_STOPS: readonly number[] = buildStops();

/** Index of the first stop ($0): "no minimum". */
export const MIN_STOP_INDEX = 0;

/** Index of the last stop ($10,000): "no maximum". */
export const MAX_STOP_INDEX = PRICE_STOPS.length - 1;

export const MAX_STOP_PRICE = PRICE_STOPS[MAX_STOP_INDEX];

/** The price at a stop index (clamped to the scale). */
export function stopIndexToPrice(index: number): number {
  const clamped = Math.min(MAX_STOP_INDEX, Math.max(MIN_STOP_INDEX, Math.round(index)));
  return PRICE_STOPS[clamped];
}

/** The index of the stop nearest to `price` (ties go to the lower stop). */
export function priceToStopIndex(price: number): number {
  if (!Number.isFinite(price) || price <= PRICE_STOPS[0]) return MIN_STOP_INDEX;
  if (price >= MAX_STOP_PRICE) return MAX_STOP_INDEX;
  let nearest = 0;
  for (let i = 1; i < PRICE_STOPS.length; i++) {
    if (Math.abs(PRICE_STOPS[i] - price) < Math.abs(PRICE_STOPS[nearest] - price)) nearest = i;
    else break;
  }
  return nearest;
}

/** Fractional stop index of `price`: linear between neighbouring stops, clamped to the scale. */
export function priceToFractionalIndex(price: number): number {
  if (!Number.isFinite(price) || price <= PRICE_STOPS[0]) return MIN_STOP_INDEX;
  if (price >= MAX_STOP_PRICE) return MAX_STOP_INDEX;
  let i = 1;
  while (i < PRICE_STOPS.length - 1 && PRICE_STOPS[i] < price) i++;
  const lo = PRICE_STOPS[i - 1];
  const hi = PRICE_STOPS[i];
  return i - 1 + (price - lo) / (hi - lo);
}

/** How far an approximate ("around") price end reaches either way: ±15%. */
export const PRICE_FUZZ = 0.15;

export type PriceBand = "in" | "over" | "under" | "out";

/**
 * Where `price` sits against a budget whose ends may be approximate:
 * "over"/"under" are inside the fuzzy margin past an approximate end,
 * "out" is beyond any margin, "in" is within the range.
 */
export function priceBand(
  price: number,
  range: { min?: number; max?: number; minFuzzy?: boolean; maxFuzzy?: boolean },
): PriceBand {
  const { min, max, minFuzzy, maxFuzzy } = range;
  if (max !== undefined && price > max) {
    return maxFuzzy && price <= max * (1 + PRICE_FUZZ) ? "over" : "out";
  }
  if (min !== undefined && price < min) {
    return minFuzzy && price >= min * (1 - PRICE_FUZZ) ? "under" : "out";
  }
  return "in";
}
