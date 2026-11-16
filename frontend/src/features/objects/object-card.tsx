import { Chip, type ChipColor } from "@/components/base/badges/chip";
import { formatPrice } from "@/lib/format";
import { getProductById } from "@/mocks/catalog";
import { useAppState } from "@/state/app-store";
import { getObject, getObjectStatus, getParentSpaces, type ObjectStatus } from "@/state/selectors";
import { cx } from "@/utils/cx";
import { OBJECT_TYPE_VISUALS } from "./object-type-visuals";
import { ProductTile } from "./product-tile";
import { useObjectDrawer } from "./use-object-drawer";

export interface ObjectCardProps {
  objectId: string;
  className?: string;
}

function statusChip(status: ObjectStatus, matchCount: number): { color: ChipColor; label: string } {
  switch (status) {
    case "not-searched":
      return { color: "neutral", label: "Not searched" };
    case "searching":
      return { color: "blue", label: "Searching…" };
    case "matches":
      return { color: "soft", label: `${matchCount} ${matchCount === 1 ? "match" : "matches"}` };
    case "no-matches":
      return { color: "rose", label: "No matches" };
    case "selected":
      return { color: "lime", label: "Selected" };
    case "in-cart":
      return { color: "lime", label: "In cart" };
    case "owned":
      return { color: "orange", label: "Owned" };
  }
}

const MAX_SPACE_CHIPS = 2;

export function ObjectCard({ objectId, className }: ObjectCardProps) {
  const state = useAppState();
  const drawer = useObjectDrawer();
  const object = getObject(state, objectId);
  if (!object) return null;

  const status = getObjectStatus(state, objectId);
  const productId = object.owned?.productId ?? object.selectedProductId;
  const product = productId ? getProductById(productId) : undefined;
  const chip = statusChip(status, object.candidateIds.length);
  const spaces = getParentSpaces(state, objectId);
  const showPrice = product && (status === "selected" || status === "owned" || status === "in-cart");
  const TypeIcon = OBJECT_TYPE_VISUALS[object.type].icon;

  return (
    <button
      type="button"
      onClick={() => drawer.open(objectId)}
      aria-label={`${object.name}, ${chip.label}. Open details`}
      className={cx(
        "flex w-full cursor-pointer flex-col gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-3 text-start outline-none",
        "transition-colors duration-150 hover:border-border-button-hover hover:bg-background-secondary-hover",
        "focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        className,
      )}
    >
      <ProductTile type={product?.type ?? object.type} size="md" />
      <span className="flex min-w-0 flex-col gap-2">
        <span className="flex items-start justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            <TypeIcon aria-hidden className="size-4 shrink-0 text-foreground-icon-tertiary" />
            <span className="truncate text-body-medium text-text-primary">{object.name}</span>
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          <Chip color={chip.color} variant="caption">
            {chip.label}
          </Chip>
          {showPrice ? (
            <bdi className="text-body-2-medium text-text-primary">{formatPrice(product.price)}</bdi>
          ) : null}
        </span>
        <span className="flex flex-wrap items-center gap-1">
          {spaces.length === 0 ? (
            <span className="text-caption-1-medium text-text-tertiary">Unassigned</span>
          ) : (
            <>
              {spaces.slice(0, MAX_SPACE_CHIPS).map((space) => (
                <Chip key={space.id} color="neutral" variant="caption" className="max-w-32 truncate">
                  {space.name}
                </Chip>
              ))}
              {spaces.length > MAX_SPACE_CHIPS ? (
                <span className="text-caption-1-medium text-text-tertiary">+{spaces.length - MAX_SPACE_CHIPS}</span>
              ) : null}
            </>
          )}
        </span>
      </span>
    </button>
  );
}
