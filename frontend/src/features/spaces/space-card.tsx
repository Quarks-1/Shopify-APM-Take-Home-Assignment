import { RiAddLine } from "@remixicon/react";
import { useNavigate } from "react-router";
import { Chip } from "@/components/base/badges/chip";
import { formatDims, formatPrice } from "@/lib/format";
import { SPACE_TYPE_OPTIONS } from "@/mocks/types";
import { useAppState } from "@/state/app-store";
import { getSpace, getSpaceEstimatedTotal } from "@/state/selectors";
import { cx } from "@/utils/cx";
import { SpacePlanPreview } from "./space-plan-preview";

const CARD =
  "flex w-72 shrink-0 flex-col gap-3 rounded-3xl border bg-background-primary-default p-3 text-start " +
  "outline-none transition-colors focus-visible:ring-2 focus-visible:ring-border-focus-ring";

export interface SpaceCardProps {
  spaceId: string;
  className?: string;
}

export function SpaceCard({ spaceId, className }: SpaceCardProps) {
  const state = useAppState();
  const navigate = useNavigate();
  const space = getSpace(state, spaceId);
  if (!space) return null;

  const typeLabel = SPACE_TYPE_OPTIONS.find((o) => o.value === space.type)?.label ?? space.type;
  const count = space.placements.length;
  const total = getSpaceEstimatedTotal(state, spaceId);

  return (
    <button
      type="button"
      onClick={() => navigate(`/spaces/${space.id}`)}
      className={cx(CARD, "cursor-pointer border-border-button-default hover:border-border-button-hover", className)}
    >
      <div className="aspect-[4/3] w-full overflow-hidden rounded-2xl bg-background-secondary-default p-3">
        <SpacePlanPreview spaceId={space.id} />
      </div>
      <div className="flex min-w-0 flex-col gap-1.5 px-1 pb-1">
        <div className="flex items-start justify-between gap-2">
          <span className="min-w-0 truncate text-headline-medium text-text-primary">{space.name}</span>
          <Chip variant="caption" color="soft">
            {typeLabel}
          </Chip>
        </div>
        <span className="text-body-regular text-text-secondary tabular-nums" dir="ltr">
          {formatDims(space.dims, space.unit)}
        </span>
        <div className="flex items-center justify-between gap-2 text-body-regular text-text-secondary">
          <span>
            {count} {count === 1 ? "object" : "objects"}
          </span>
          <span className="tabular-nums">Est. {formatPrice(total)}</span>
        </div>
      </div>
    </button>
  );
}

export function NewSpaceCard({ className }: { className?: string }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate("/spaces/new")}
      className={cx(
        CARD,
        "cursor-pointer items-center justify-center self-stretch border-dashed border-border-button-default",
        "min-h-64 text-text-secondary hover:border-border-button-hover hover:bg-background-secondary-default hover:text-text-primary",
        className,
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-full bg-background-tertiary-default">
        <RiAddLine aria-hidden className="size-5 text-foreground-icon-secondary" />
      </span>
      <span className="text-headline-medium text-text-primary">New space</span>
    </button>
  );
}
