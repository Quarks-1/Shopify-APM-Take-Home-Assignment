import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, UIEvent } from "react";
import { createPortal } from "react-dom";
import { RiAddLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { CloseButton } from "@/components/base/buttons/close-button";
import { Checkbox } from "@/components/base/checkbox/checkbox";
import { Input } from "@/components/base/input/input";
import { Select, SelectItem } from "@/components/base/select/select";
import { Spinner } from "@/components/base/spinner/spinner";
import { useDirection } from "@/components/foundations/direction/direction";
import { ScrollFade, useScrollFade } from "@/components/foundations/scroll-fade/scroll-fade";
import {
  updateObject,
  type Material,
  type ObjectType,
  type ObjectUpdate,
  type PlacedObject,
  type SpaceSummary,
  type Style,
} from "@/lib/spaces";
import { cx } from "@/utils/cx";
import "./object-attribute-menu.css";

export const OBJECT_ATTRIBUTE_FIELDS = [
  "x",
  "y",
  "z",
  "weight",
  "object_type",
  "material",
  "style",
  "price_limit",
  "similar_items",
  "spaces",
] as const;

const OBJECT_TYPES: Array<{ id: ObjectType; label: string }> = [
  { id: "furniture", label: "Furniture" },
  { id: "poster", label: "Poster" },
];

const MATERIALS: Array<{ id: Material; label: string }> = [
  { id: "metal", label: "Metal" },
  { id: "wood", label: "Wood" },
  { id: "fabric", label: "Fabric" },
];

const STYLES: Array<{ id: Style; label: string }> = [
  { id: "modern", label: "Modern" },
  { id: "vintage", label: "Vintage" },
  { id: "brutalist", label: "Brutalist" },
];

interface SimilarDraft {
  key: string;
  id?: number;
  name: string;
  image: string;
}

interface Draft {
  x: string;
  y: string;
  z: string;
  weight: string;
  object_type: ObjectType;
  material: Material;
  style: Style;
  price_limit: string;
  similar_items: SimilarDraft[];
  space_ids: number[];
}

function toDraft(object: PlacedObject): Draft {
  return {
    x: object.x,
    y: object.y,
    z: object.z ?? "",
    weight: object.weight,
    object_type: object.object_type,
    material: object.material,
    style: object.style,
    price_limit: object.price_limit,
    similar_items: object.similar_items.map((item) => ({
      key: `item-${item.id}`,
      id: item.id,
      name: item.name,
      image: item.image,
    })),
    space_ids: [...object.space_ids],
  };
}

let similarKey = 0;

function nextSimilarKey(): string {
  similarKey += 1;
  return `new-similar-${similarKey}`;
}

function supportsAnchorPosition(): boolean {
  return typeof CSS !== "undefined" && CSS.supports("position-area", "block-end span-inline-end");
}

function clampToViewport(x: number, y: number, width: number, height: number) {
  const margin = 8;
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const maxTop = Math.max(margin, window.innerHeight - height - margin);
  let left = x;
  let top = y;
  if (left + width > window.innerWidth - margin) left = x - width;
  if (top + height > window.innerHeight - margin) top = y - height;
  return {
    left: Math.min(Math.max(left, margin), maxLeft),
    top: Math.min(Math.max(top, margin), maxTop),
  };
}

function isInsideMenuChrome(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(
      "[data-object-attribute-menu], [role='listbox'], [role='option'], [data-placement]",
    ),
  );
}

export interface ObjectAttributeMenuProps {
  object: PlacedObject;
  space: SpaceSummary;
  spaces: SpaceSummary[];
  point: { x: number; y: number };
  onClose: () => void;
  onUpdated: (object: PlacedObject) => void;
}

export function ObjectAttributeMenu({
  object,
  space,
  spaces,
  point,
  onClose,
  onUpdated,
}: ObjectAttributeMenuProps) {
  const direction = useDirection();
  const fade = useScrollFade();
  const [draft, setDraft] = useState(() => toDraft(object));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const didFocus = useRef(false);
  const [moreBelow, setMoreBelow] = useState(false);

  useEffect(() => {
    setDraft(toDraft(object));
  }, [object]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (isInsideMenuChrome(event.target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const listboxOpen = [...document.querySelectorAll("[role='listbox']")].some((node) => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      });
      if (listboxOpen) return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    const anchor = anchorRef.current;
    if (!menu || !anchor) return;
    anchor.style.left = `${point.x}px`;
    anchor.style.top = `${point.y}px`;
    if (!didFocus.current) {
      menu.focus();
      didFocus.current = true;
    }

    const placeWithScript = () => {
      const rect = menu.getBoundingClientRect();
      const next = clampToViewport(point.x, point.y, rect.width, rect.height);
      menu.style.left = `${next.left}px`;
      menu.style.top = `${next.top}px`;
    };

    if (!supportsAnchorPosition()) {
      placeWithScript();
      const observer = new ResizeObserver(placeWithScript);
      observer.observe(menu);
      window.addEventListener("resize", placeWithScript);
      return () => {
        observer.disconnect();
        window.removeEventListener("resize", placeWithScript);
      };
    }

    menu.style.left = "";
    menu.style.top = "";
    const frame = window.requestAnimationFrame(() => {
      const rect = menu.getBoundingClientRect();
      const overflow =
        rect.left < 8 ||
        rect.top < 8 ||
        rect.right > window.innerWidth - 8 ||
        rect.bottom > window.innerHeight - 8;
      if (overflow) placeWithScript();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [point.x, point.y]);

  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    setMoreBelow(scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1);
  }, [draft.similar_items.length]);

  const onMenuScroll = (event: UIEvent<HTMLDivElement>) => {
    fade.onScroll(event);
    const node = event.currentTarget;
    setMoreBelow(node.scrollTop + node.clientHeight < node.scrollHeight - 1);
  };

  const update = (partial: Partial<Draft>) => {
    setStatus(null);
    setError(null);
    setDraft((current) => ({ ...current, ...partial }));
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload: ObjectUpdate = {
      x: draft.x.trim(),
      y: draft.y.trim(),
      z: draft.z.trim() === "" ? null : draft.z.trim(),
      weight: draft.weight.trim(),
      object_type: draft.object_type,
      material: draft.material,
      style: draft.style,
      price_limit: draft.price_limit.trim(),
      similar_items: draft.similar_items.map((item) => ({
        ...(item.id ? { id: item.id } : {}),
        name: item.name.trim(),
        image: item.image.trim(),
      })),
      space_ids: draft.space_ids,
      editing_space_id: space.id,
    };
    try {
      const saved = await updateObject(object.id, payload);
      onUpdated(saved);
      setStatus("Saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save this object");
    } finally {
      setSaving(false);
    }
  };

  const menu = (
    <>
      <div ref={anchorRef} className="object-attribute-menu-anchor" aria-hidden />
      <div
        ref={menuRef}
        role="dialog"
        aria-label="Object attributes"
        tabIndex={-1}
        dir={direction}
        data-object-attribute-menu
        onContextMenu={(event) => event.preventDefault()}
        className={cx(
          "object-attribute-menu z-100 flex flex-col overflow-hidden rounded-2xl border border-border-button-default bg-background-primary-default text-text-primary shadow-dropdown outline-none",
        )}
      >
        <form onSubmit={(event) => void onSubmit(event)} className="flex flex-col">
          <header className="flex shrink-0 items-start justify-between gap-2 border-b border-separator-border ps-3 pe-2 pt-3 pb-2">
            <div className="min-w-0">
              <h2 className="text-body-medium text-text-primary">Object attributes</h2>
              <p className="text-caption-1-medium text-text-tertiary">
                <bdi>{object.id}</bdi>
                <span aria-hidden> · </span>
                Coordinates snap to the {space.dimension_unit === "m" ? "meter" : "foot"} grid
              </p>
            </div>
            <CloseButton size="xs" aria-label="Close object attributes" onClick={onClose} />
          </header>

          <div className="relative">
            <div
              ref={scrollerRef}
              onScroll={onMenuScroll}
              className="max-h-72 overflow-y-auto overscroll-contain ps-3 pe-3"
            >
              <div className="flex flex-col gap-3 py-3">
                <div className="flex flex-col gap-2" data-attribute="x">
                  <Input
                    size="small"
                    label="X"
                    inputDir="ltr"
                    value={draft.x}
                    onChange={(value) => update({ x: value })}
                    isDisabled={saving}
                    autoComplete="off"
                  />
                </div>
                <div data-attribute="y">
                  <Input
                    size="small"
                    label="Y"
                    inputDir="ltr"
                    value={draft.y}
                    onChange={(value) => update({ y: value })}
                    isDisabled={saving}
                    autoComplete="off"
                  />
                </div>
                <div data-attribute="z">
                  <Input
                    size="small"
                    label="Z"
                    hint="Optional"
                    inputDir="ltr"
                    value={draft.z}
                    onChange={(value) => update({ z: value })}
                    isDisabled={saving}
                    autoComplete="off"
                  />
                </div>
                <div data-attribute="weight">
                  <Input
                    size="small"
                    label="Weight"
                    inputDir="ltr"
                    value={draft.weight}
                    onChange={(value) => update({ weight: value })}
                    isDisabled={saving}
                    autoComplete="off"
                  />
                </div>
                <div data-attribute="object_type">
                  <Select
                    label="Object type"
                    size="sm"
                    selectedKey={draft.object_type}
                    isDisabled={saving}
                    onSelectionChange={(key) => {
                      if (key != null) update({ object_type: String(key) as ObjectType });
                    }}
                  >
                    {OBJECT_TYPES.map((option) => (
                      <SelectItem key={option.id} id={option.id}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </Select>
                </div>
                <div data-attribute="material">
                  <Select
                    label="Material"
                    size="sm"
                    selectedKey={draft.material}
                    isDisabled={saving}
                    onSelectionChange={(key) => {
                      if (key != null) update({ material: String(key) as Material });
                    }}
                  >
                    {MATERIALS.map((option) => (
                      <SelectItem key={option.id} id={option.id}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </Select>
                </div>
                <div data-attribute="style">
                  <Select
                    label="Style"
                    size="sm"
                    selectedKey={draft.style}
                    isDisabled={saving}
                    onSelectionChange={(key) => {
                      if (key != null) update({ style: String(key) as Style });
                    }}
                  >
                    {STYLES.map((option) => (
                      <SelectItem key={option.id} id={option.id}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </Select>
                </div>
                <div data-attribute="price_limit">
                  <Input
                    size="small"
                    label="Price limit"
                    inputDir="ltr"
                    value={draft.price_limit}
                    onChange={(value) => update({ price_limit: value })}
                    isDisabled={saving}
                    autoComplete="off"
                  />
                </div>

                <fieldset data-attribute="similar_items" className="flex flex-col gap-2">
                  <legend className="text-caption-1-semibold text-text-secondary">Similar items</legend>
                  {draft.similar_items.map((item, index) => (
                    <div
                      key={item.key}
                      className="flex flex-col gap-2 rounded-xl border border-separator-border p-2"
                    >
                      <Input
                        size="small"
                        label="Name"
                        value={item.name}
                        onChange={(value) => {
                          const similar_items = draft.similar_items.map((row, rowIndex) =>
                            rowIndex === index ? { ...row, name: value } : row,
                          );
                          update({ similar_items });
                        }}
                        isDisabled={saving}
                        autoComplete="off"
                      />
                      <Input
                        size="small"
                        label="Image"
                        type="url"
                        inputDir="ltr"
                        value={item.image}
                        onChange={(value) => {
                          const similar_items = draft.similar_items.map((row, rowIndex) =>
                            rowIndex === index ? { ...row, image: value } : row,
                          );
                          update({ similar_items });
                        }}
                        isDisabled={saving}
                        autoComplete="off"
                      />
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={saving}
                        onClick={() =>
                          update({
                            similar_items: draft.similar_items.filter((row) => row.key !== item.key),
                          })
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="secondary"
                    size="xs"
                    leadingIcon={RiAddLine}
                    disabled={saving}
                    onClick={() =>
                      update({
                        similar_items: [
                          ...draft.similar_items,
                          { key: nextSimilarKey(), name: "", image: "" },
                        ],
                      })
                    }
                  >
                    Add similar item
                  </Button>
                </fieldset>

                <fieldset data-attribute="spaces" className="flex flex-col gap-2">
                  <legend className="text-caption-1-semibold text-text-secondary">Parent spaces</legend>
                  {spaces.map((option) => (
                    <Checkbox
                      key={option.id}
                      size="sm"
                      isSelected={draft.space_ids.includes(option.id)}
                      isDisabled={saving}
                      onChange={(selected) => {
                        const space_ids = selected
                          ? [...draft.space_ids, option.id]
                          : draft.space_ids.filter((id) => id !== option.id);
                        update({ space_ids });
                      }}
                    >
                      {option.label}
                    </Checkbox>
                  ))}
                </fieldset>
              </div>
            </div>
            <ScrollFade progress={fade.progress} />
            {moreBelow ? (
              <div
                aria-hidden
                className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-6 bg-linear-to-t from-background-primary-default to-transparent"
              />
            ) : null}
          </div>

          <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-separator-border ps-3 pe-3 py-2">
            <p className="min-w-0 text-caption-1-medium text-text-tertiary" role={error ? "alert" : "status"}>
              {error ? <span className="text-text-error-primary">{error}</span> : status}
            </p>
            <Button type="submit" variant="primary" size="small" disabled={saving}>
              {saving ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner size="xs" aria-hidden />
                  <span>Saving</span>
                </span>
              ) : (
                "Save"
              )}
            </Button>
          </footer>
        </form>
      </div>
    </>
  );

  return createPortal(menu, document.body);
}
