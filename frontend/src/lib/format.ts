import type { Dims, LengthUnit, ObjectDims } from "@/mocks/types";

const CM_PER_INCH = 2.54;
const INCHES_PER_FOOT = 12;

export function formatPrice(amount: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
  }).format(amount);
}

export function toDisplayLength(inches: number, unit: LengthUnit): number {
  switch (unit) {
    case "cm":
      return Math.round(inches * CM_PER_INCH);
    case "ft":
      return Math.round((inches / INCHES_PER_FOOT) * 100) / 100;
    case "in":
      return Math.round(inches * 10) / 10;
  }
}

export function fromDisplayLength(value: number, unit: LengthUnit): number {
  switch (unit) {
    case "cm":
      return Math.round((value / CM_PER_INCH) * 100) / 100;
    case "ft":
      return Math.round(value * INCHES_PER_FOOT * 100) / 100;
    case "in":
      return value;
  }
}

/** Stored inches for a form field: unchanged when the field still shows the stored value, else converted back. */
export function fromDisplayLengthKeeping(value: number, unit: LengthUnit, stored: number | undefined): number {
  return stored !== undefined && value === toDisplayLength(stored, unit) ? stored : fromDisplayLength(value, unit);
}

export function formatLength(inches: number, unit: LengthUnit): string {
  switch (unit) {
    case "cm":
      return `${toDisplayLength(inches, "cm")} cm`;
    case "ft":
      // Under a foot reads better in inches.
      return inches >= INCHES_PER_FOOT ? `${toDisplayLength(inches, "ft")}′` : `${toDisplayLength(inches, "in")}″`;
    case "in":
      return `${toDisplayLength(inches, "in")}″`;
  }
}

/** "x × y × z unit", skipping unknown sides; "" when no side is known (some catalog products). */
export function formatDims(dims: Dims | ObjectDims | Partial<Dims> | undefined, unit: LengthUnit): string {
  if (!dims) return "";
  const parts = [dims.x, dims.y, dims.z].filter((p): p is number => p !== undefined);
  if (parts.length === 0) return "";
  return `${parts.map((p) => toDisplayLength(p, unit)).join(" × ")} ${unit}`;
}

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return new Date(then).toLocaleDateString();
}

/** An amount in the currency's minor units (cents for USD, yen for JPY) as a price. */
export function formatMinor(amount: number, currency: string): string {
  let format: Intl.NumberFormat;
  try {
    format = new Intl.NumberFormat("en-US", { style: "currency", currency });
  } catch {
    // Not an ISO 4217 code Intl knows: show the raw amount.
    return `${amount} ${currency}`;
  }
  const digits = format.resolvedOptions().maximumFractionDigits ?? 2;
  return format.format(amount / 10 ** digits);
}
