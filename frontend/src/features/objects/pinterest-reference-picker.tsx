import { useEffect, useMemo, useState } from "react";
import { RiCheckLine, RiImageLine } from "@remixicon/react";
import { Chip } from "@/components/base/badges/chip";
import { Button } from "@/components/base/buttons/button";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { toast } from "@/components/base/toast/toast";
import { GroupIcon, groupLabel } from "@/features/home/vibes-inspiration";
import { listInspirations, listSpaceInspirations, type ApiInspiration } from "@/lib/api";
import { OBJECT_TYPE_OPTIONS, type ObjectType } from "@/mocks/types";
import { cx } from "@/utils/cx";

export const MAX_REFERENCE_PINS = 5;
const TRUSTED_IMAGE_PREFIX = "https://i.pinimg.com/";
const INITIAL_MANUAL_PINS = 6;

export interface ParsedPinGroups {
  similar: ApiInspiration[];
  category: ApiInspiration[];
}

export interface PinterestReferencePickerProps {
  spaceIds: string[];
  objectType: ObjectType;
  selectedPinIds: string[];
  onChange: (nextPinIds: string[]) => void;
  /** Ranked pins returned by POST /api/objects/parse-prompt/, when available. */
  parsedPins?: ParsedPinGroups | null;
  /** Heading style: standalone post-prompt card vs sub-block inside Similar items. */
  variant?: "featured" | "inline";
}

interface SelectablePinCardProps {
  pin: ApiInspiration;
  selected: boolean;
  selectionOrder?: number;
  atLimit: boolean;
  onToggle: () => void;
}

function SelectablePinCard({ pin, selected, selectionOrder, atLimit, onToggle }: SelectablePinCardProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = pin.imageUrl.startsWith(TRUSTED_IMAGE_PREFIX) && !imageFailed;
  const fallbackLabel = groupLabel(pin.primaryType);
  const title = pin.item ? pin.item.charAt(0).toUpperCase() + pin.item.slice(1) : fallbackLabel;
  const subtitle = [pin.colors[0], pin.materials[0] ?? pin.styles[0]].filter(Boolean).join(" · ") || fallbackLabel;

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${selected ? "Deselect" : "Select"} Pinterest reference: ${title}`}
      title={pin.description || title}
      onClick={onToggle}
      className={cx(
        "group relative flex flex-col gap-1.5 rounded-2xl border p-1.5 text-start transition-[border-color,background-color,box-shadow,opacity] duration-150",
        "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        selected
          ? "border-accent-500 bg-accent-50 ring-2 ring-accent-500"
          : "border-border-button-default bg-background-primary-default hover:border-border-button-hover hover:bg-background-secondary-default",
        !selected && atLimit && "opacity-60",
      )}
    >
      <div className="relative aspect-[3/4] w-full overflow-hidden rounded-xl bg-background-secondary-default">
        {showImage ? (
          <img
            src={pin.imageUrl}
            alt={pin.description || title}
            className="size-full object-cover"
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="flex size-full items-center justify-center">
            <GroupIcon group={pin.primaryType} className="size-7" />
          </div>
        )}
        <span
          aria-hidden
          className={cx(
            "absolute end-1.5 top-1.5 flex size-6 items-center justify-center rounded-full border transition-colors",
            selected
              ? "border-accent-600 bg-accent-600 text-text-white shadow-xs"
              : "border-border-button-default bg-background-primary-default text-transparent group-hover:border-border-button-hover",
          )}
        >
          {selected && selectionOrder !== undefined ? (
            <span className="text-caption-2-semibold">{selectionOrder}</span>
          ) : (
            <RiCheckLine className="size-3.5" />
          )}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 px-1 pb-0.5">
        <span className="truncate text-caption-1-semibold text-text-primary">{title}</span>
        <span className="truncate text-caption-2-regular text-text-secondary">{subtitle}</span>
      </div>
    </button>
  );
}

export function PinterestReferencePicker({
  spaceIds,
  objectType,
  selectedPinIds,
  onChange,
  parsedPins,
  variant = "inline",
}: PinterestReferencePickerProps) {
  const [boardPins, setBoardPins] = useState<ApiInspiration[] | null>(null);
  const [showAllCategory, setShowAllCategory] = useState(false);

  const spaceKey = spaceIds.join(",");
  const hasSelected = selectedPinIds.length > 0;

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        if (spaceIds.length > 0) {
          const lists = await Promise.all(spaceIds.map((sid) => listSpaceInspirations(sid, controller.signal)));
          const seen = new Set<string>();
          const merged: ApiInspiration[] = [];
          for (const list of lists) {
            for (const pin of list) {
              if (!pin.categorized || seen.has(pin.pinId)) continue;
              seen.add(pin.pinId);
              merged.push(pin);
            }
          }
          // If the object holds selected pins from another space, resolve their metadata too.
          if (selectedPinIds.some((id) => !seen.has(id))) {
            const globalPins = await listInspirations(controller.signal);
            for (const pin of globalPins) {
              if (selectedPinIds.includes(pin.pinId) && !seen.has(pin.pinId)) {
                seen.add(pin.pinId);
                merged.push(pin);
              }
            }
          }
          if (!controller.signal.aborted) setBoardPins(merged);
        } else if (hasSelected) {
          const globalPins = await listInspirations(controller.signal);
          if (!controller.signal.aborted) setBoardPins(globalPins);
        } else {
          setBoardPins([]);
        }
      } catch {
        if (!controller.signal.aborted) setBoardPins([]);
      }
    };
    void load();
    return () => controller.abort();
  }, [spaceKey, hasSelected, selectedPinIds, spaceIds]);

  const typeLabel = OBJECT_TYPE_OPTIONS.find((o) => o.value === objectType)?.label ?? objectType;

  const { primaryPins, extraPins, totalBoardPins } = useMemo(() => {
    const byId = new Map<string, ApiInspiration>();
    for (const p of boardPins ?? []) byId.set(p.pinId, p);
    if (parsedPins) {
      for (const p of parsedPins.similar) byId.set(p.pinId, p);
      for (const p of parsedPins.category) byId.set(p.pinId, p);
    }

    if (parsedPins) {
      const seen = new Set<string>();
      const primary: ApiInspiration[] = [];
      // Keep any already-selected pins visible even if outside top recommendations.
      for (const pid of selectedPinIds) {
        const found = byId.get(pid);
        if (found && !seen.has(found.pinId)) {
          seen.add(found.pinId);
          primary.push(found);
        }
      }
      for (const pin of parsedPins.similar) {
        if (!seen.has(pin.pinId)) {
          seen.add(pin.pinId);
          primary.push(pin);
        }
      }
      const extra = parsedPins.category.filter((pin) => !seen.has(pin.pinId));
      return {
        primaryPins: primary,
        extraPins: extra,
        totalBoardPins: (boardPins?.length ?? 0) || primary.length + extra.length,
      };
    }

    const all = boardPins ?? [];
    const seen = new Set<string>();
    const selectedResolved: ApiInspiration[] = [];
    for (const pid of selectedPinIds) {
      const found = byId.get(pid);
      if (found && !seen.has(found.pinId)) {
        seen.add(found.pinId);
        selectedResolved.push(found);
      }
    }
    const matchingCategory = all.filter((p) => !seen.has(p.pinId) && p.categories.includes(objectType));
    const initialSlice = Math.max(0, INITIAL_MANUAL_PINS - selectedResolved.length);
    return {
      primaryPins: [...selectedResolved, ...matchingCategory.slice(0, initialSlice)],
      extraPins: matchingCategory.slice(initialSlice),
      totalBoardPins: all.length,
    };
  }, [boardPins, objectType, parsedPins, selectedPinIds]);

  const togglePin = (pinId: string) => {
    if (selectedPinIds.includes(pinId)) {
      onChange(selectedPinIds.filter((id) => id !== pinId));
      return;
    }
    if (selectedPinIds.length >= MAX_REFERENCE_PINS) {
      toast(`You can select up to ${MAX_REFERENCE_PINS} image references`);
      return;
    }
    onChange([...selectedPinIds, pinId]);
  };

  // Loading state before board pins resolve (when no parsedPins exist yet).
  if (boardPins === null && !parsedPins && spaceIds.length > 0) {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-body-2-medium text-text-secondary">Image references</span>
        </div>
        <div className="grid grid-cols-3 gap-2.5">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="aspect-[3/4] w-full rounded-2xl" />
          ))}
        </div>
      </div>
    );
  }

  // No linked Pinterest board on the assigned space(s), and nothing selected.
  if (totalBoardPins === 0 && primaryPins.length === 0 && extraPins.length === 0) {
    if (variant === "featured") return null;
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-dashed border-border-button-default px-3 py-2.5">
        <RiImageLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
        <p className="text-body-2-regular text-text-tertiary">
          {spaceIds.length === 0
            ? "Assign this object to a space with a linked Pinterest board to pick image references."
            : "Import a Pinterest board from Space settings to pick visual image references."}
        </p>
      </div>
    );
  }

  const visiblePins = showAllCategory ? [...primaryPins, ...extraPins] : primaryPins;
  const atLimit = selectedPinIds.length >= MAX_REFERENCE_PINS;

  return (
    <div
      className={cx(
        "flex flex-col gap-3",
        variant === "featured" &&
          "rounded-3xl border border-border-button-default bg-background-secondary-default p-4",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h4 className={variant === "featured" ? "text-headline-medium text-text-primary" : "text-body-medium text-text-primary"}>
            {variant === "featured" ? "Similar from your Pinterest board" : "Pinterest image references"}
          </h4>
          <p className="text-body-2-regular text-text-secondary">
            {primaryPins.length > 0
              ? "Select pins that match the look you want to steer visual product search."
              : `No exact item matches on your board yet — browse ${typeLabel.toLowerCase()} pins below if one fits.`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Chip variant="caption" color={selectedPinIds.length > 0 ? "blue" : "neutral"}>
            {selectedPinIds.length}/{MAX_REFERENCE_PINS} selected
          </Chip>
          {selectedPinIds.length > 0 ? (
            <Button variant="ghost" size="small" onClick={() => onChange([])}>
              Clear
            </Button>
          ) : null}
        </div>
      </div>

      {visiblePins.length > 0 ? (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
          {visiblePins.map((pin) => {
            const idx = selectedPinIds.indexOf(pin.pinId);
            const isSelected = idx !== -1;
            return (
              <SelectablePinCard
                key={pin.pinId}
                pin={pin}
                selected={isSelected}
                selectionOrder={isSelected ? idx + 1 : undefined}
                atLimit={atLimit}
                onToggle={() => togglePin(pin.pinId)}
              />
            );
          })}
        </div>
      ) : extraPins.length === 0 ? (
        <p className="text-body-2-regular text-text-tertiary">
          None of the pins on your linked board match {typeLabel.toLowerCase()} yet.
        </p>
      ) : null}

      {extraPins.length > 0 ? (
        <div className="flex items-center justify-start">
          <Button
            variant="secondary"
            size="small"
            onClick={() => setShowAllCategory((prev) => !prev)}
          >
            {showAllCategory
              ? `Show fewer ${typeLabel.toLowerCase()} pins`
              : `Browse all ${typeLabel.toLowerCase()} pins (${extraPins.length} more)`}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
