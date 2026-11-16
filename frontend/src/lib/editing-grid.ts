export type DimensionUnit = "ft" | "m";

/** How many grid cells span one axis. Object coordinates use the space unit; the increment is inches. */
export function gridCellCount(span: string, incrementInches: string, unit: DimensionUnit): number {
  const spanValue = Number(span);
  const increment = Number(incrementInches);
  if (!Number.isFinite(spanValue) || !Number.isFinite(increment) || spanValue <= 0 || increment <= 0) {
    return 1;
  }
  const spanInches = unit === "m" ? spanValue / 0.0254 : spanValue * 12;
  return spanInches / increment;
}

export function formatInches(value: string): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return `${value} in`;
  const text = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(number);
  return `${text} in`;
}
