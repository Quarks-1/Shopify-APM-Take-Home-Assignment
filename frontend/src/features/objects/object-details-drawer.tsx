import { useEffect, useRef, useState } from "react";
import { RiAddLine, RiExternalLinkLine } from "@remixicon/react";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Checkbox } from "@/components/base/checkbox/checkbox";
import { Drawer, DrawerBody, DrawerFooter, DrawerHeader } from "@/components/base/drawer/drawer";
import { Input } from "@/components/base/input/input";
import { AlertDialog } from "@/components/base/modal/modal";
import { NumberField } from "@/components/base/number-field/number-field";
import { Select, SelectItem } from "@/components/base/select/select";
import { Tag, TagGroup } from "@/components/base/tag-group/tag-group";
import { formatPrice, formatRelativeTime, fromDisplayLength, toDisplayLength } from "@/lib/format";
import {
  MATERIAL_OPTIONS,
  OBJECT_TYPE_OPTIONS,
  STYLE_OPTIONS,
  type LengthUnit,
  type ObjectType,
  type SpaceObject,
} from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { getObject, getParentSpaces, getProduct, getSelectedProduct, getStore } from "@/state/selectors";
import { ProductSize } from "./product-card";
import { ProductTile } from "./product-tile";
import { useObjectDrawer } from "./use-object-drawer";
import { PriceRangeSlider, type PriceRange } from "./price-range-slider";
import { useObjectSearch } from "./use-object-search";

const ANY = "any";

function parseOption<T extends string>(options: { value: T }[], key: string | number | null): T | undefined {
  return options.find((o) => o.value === key)?.value;
}

/** NumberField reports an empty value as NaN. */
function numberOrUndefined(value: number): number | undefined {
  return Number.isNaN(value) ? undefined : value;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-separator-border pt-5 first:border-t-0 first:pt-0">
      <h3 className="text-headline-medium text-text-primary">{title}</h3>
      {children}
    </section>
  );
}

interface ObjectFormProps {
  object: SpaceObject;
  isNew: boolean;
  /** Close the drawer; with `discard` the object is deleted first. */
  onFinish: (opts?: { discard?: boolean }) => void;
}

function ObjectForm({ object, isNew, onFinish }: ObjectFormProps) {
  const state = useAppState();
  const actions = useAppActions();
  const search = useObjectSearch();

  const initialSpaceIds = getParentSpaces(state, object.id).map((s) => s.id);
  const parentSpace = getParentSpaces(state, object.id)[0];
  const unit: LengthUnit = parentSpace?.unit ?? "in";
  const isWall = parentSpace?.type === "wall";
  const owned = object.source === "imported";
  const purchase = object.owned;
  const ownedProduct = purchase ? getProduct(state, purchase.productId) : undefined;
  const ownedStore = ownedProduct ? getStore(state, ownedProduct.storeId) : undefined;
  // The live object: a search may finish (and pick a product) while the drawer is open.
  const selectedProduct = owned ? undefined : getSelectedProduct(state, getObject(state, object.id) ?? object);
  const selectedStore = selectedProduct ? getStore(state, selectedProduct.storeId) : undefined;

  const [name, setName] = useState(object.name);
  const [type, setType] = useState<ObjectType>(object.type);
  const [x, setX] = useState(toDisplayLength(object.dims.x, unit));
  const [y, setY] = useState(object.dims.y === undefined ? NaN : toDisplayLength(object.dims.y, unit));
  const [z, setZ] = useState(object.dims.z === undefined ? NaN : toDisplayLength(object.dims.z, unit));
  const [weight, setWeight] = useState(object.weight ?? NaN);
  const [material, setMaterial] = useState<string>(object.material ?? ANY);
  const [style, setStyle] = useState<string>(object.style ?? ANY);
  const [priceRange, setPriceRange] = useState<PriceRange>({ min: object.priceMin, max: object.priceLimit });
  const [similar, setSimilar] = useState(object.similarItems);
  const [similarDraft, setSimilarDraft] = useState("");
  const [spaceIds, setSpaceIds] = useState(initialSpaceIds);
  const [submitted, setSubmitted] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const errors = {
    name: name.trim() ? undefined : "Name is required",
    x: x > 0 ? undefined : "Width must be greater than 0",
    z: !isWall || z > 0 ? undefined : "Height must be greater than 0",
    y: isWall || y > 0 ? undefined : "Depth must be greater than 0",
  };
  const invalid = Boolean(errors.name || errors.x || errors.y || errors.z);

  const addSimilar = () => {
    const value = similarDraft.trim();
    if (!value) return;
    setSimilar((prev) => (prev.some((s) => s.toLowerCase() === value.toLowerCase()) ? prev : [...prev, value]));
    setSimilarDraft("");
  };

  /** Persists the form; returns false when validation fails. */
  const save = (): boolean => {
    setSubmitted(true);
    if (invalid) return false;
    actions.updateObject(object.id, {
      name: name.trim(),
      type,
      dims: {
        x: fromDisplayLength(x, unit),
        y: Number.isNaN(y) ? undefined : fromDisplayLength(y, unit),
        z: Number.isNaN(z) ? undefined : fromDisplayLength(z, unit),
      },
      weight: numberOrUndefined(weight),
      material: parseOption(MATERIAL_OPTIONS, material),
      style: parseOption(STYLE_OPTIONS, style),
      priceMin: priceRange.min,
      priceLimit: priceRange.max,
      similarItems: similar,
    });
    for (const space of state.spaces) {
      const was = initialSpaceIds.includes(space.id);
      const want = spaceIds.includes(space.id);
      if (want && !was) actions.placeObject(space.id, object.id);
      if (!want && was) actions.removeFromSpace(space.id, object.id);
    }
    return true;
  };

  const showError = (message: string | undefined) => (submitted ? message : undefined);

  return (
    <>
      <DrawerHeader title={isNew ? "New object" : object.name} />
      <DrawerBody className="flex flex-col gap-5 py-4">
        <Section title="Basics">
          <Input
            label="Name"
            value={name}
            onChange={setName}
            isRequired
            isInvalid={Boolean(showError(errors.name))}
            errorMessage={errors.name}
            autoFocus={isNew}
          />
          <Select
            label="Type"
            className="w-full"
            selectedKey={type}
            onSelectionChange={(key) => setType(parseOption(OBJECT_TYPE_OPTIONS, key) ?? type)}
          >
            {OBJECT_TYPE_OPTIONS.map((o) => (
              <SelectItem key={o.value} id={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </Select>
        </Section>

        <Section title="Size">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <NumberField
              label={`Width (${unit})`}
              value={x}
              onChange={setX}
              minValue={0}
              isRequired
              isInvalid={Boolean(showError(errors.x))}
              hint={showError(errors.x)}
            />
            <NumberField
              label={`Height (${unit})`}
              value={z}
              onChange={setZ}
              minValue={0}
              isRequired={isWall}
              isInvalid={Boolean(showError(errors.z))}
              hint={showError(errors.z) ?? (isWall ? undefined : "Optional")}
            />
            <NumberField
              label={`Depth (${unit})`}
              value={y}
              onChange={setY}
              minValue={0}
              isRequired={!isWall}
              isInvalid={Boolean(showError(errors.y))}
              hint={showError(errors.y) ?? (isWall ? "Optional" : undefined)}
            />
          </div>
          <NumberField label="Weight (lb)" value={weight} onChange={setWeight} minValue={0} hint="Optional" />
        </Section>

        <Section title="Style">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Select label="Material" className="w-full" selectedKey={material} onSelectionChange={(k) => setMaterial(String(k))}>
              <SelectItem id={ANY}>Any</SelectItem>
              {MATERIAL_OPTIONS.map((o) => (
                <SelectItem key={o.value} id={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </Select>
            <Select label="Style" className="w-full" selectedKey={style} onSelectionChange={(k) => setStyle(String(k))}>
              <SelectItem id={ANY}>Any</SelectItem>
              {STYLE_OPTIONS.map((o) => (
                <SelectItem key={o.value} id={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </Select>
          </div>
          <PriceRangeSlider value={priceRange} onChange={setPriceRange} />
        </Section>

        <Section title="Similar items">
          <TagGroup
            aria-label="Similar items"
            items={similar.map((s) => ({ id: s }))}
            emptyState="No similar items yet"
            onRemove={(keys) => setSimilar((prev) => prev.filter((s) => !keys.has(s)))}
          >
            {(item) => <Tag id={item.id}>{item.id}</Tag>}
          </TagGroup>
          <div className="flex items-end gap-2">
            <Input
              aria-label="Add a similar item"
              placeholder="e.g. Eames lounge chair"
              className="min-w-0 flex-1"
              value={similarDraft}
              onChange={setSimilarDraft}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addSimilar();
                }
              }}
            />
            <Button variant="secondary" leadingIcon={RiAddLine} onClick={addSimilar}>
              Add
            </Button>
          </div>
          <p className="text-body-2-regular text-text-tertiary">Image references are coming later.</p>
        </Section>

        <Section title="Parent spaces">
          {state.spaces.length === 0 ? (
            <p className="text-body-regular text-text-secondary">No spaces yet.</p>
          ) : (
            <div role="group" aria-label="Parent spaces" className="flex flex-col gap-2">
              {state.spaces.map((space) => (
                <Checkbox
                  key={space.id}
                  isSelected={spaceIds.includes(space.id)}
                  onChange={(checked) =>
                    setSpaceIds((prev) => (checked ? [...prev, space.id] : prev.filter((id) => id !== space.id)))
                  }
                >
                  {space.name}
                </Checkbox>
              ))}
            </div>
          )}
        </Section>

        {selectedProduct ? (
          <Section title="Selected product">
            <div className="flex items-start gap-3">
              <ProductTile type={selectedProduct.type} imageUrl={selectedProduct.imageUrl} size="sm" />
              <dl className="grid min-w-0 flex-1 grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-body-regular">
                <dt className="text-text-secondary">Product</dt>
                <dd className="min-w-0 text-text-primary">{selectedProduct.title}</dd>
                <dt className="text-text-secondary">Store</dt>
                <dd className="min-w-0 text-text-primary">{selectedStore?.name ?? "Unknown store"}</dd>
                <dt className="text-text-secondary">Price</dt>
                <dd className="text-text-primary">
                  <bdi>{formatPrice(selectedProduct.price)}</bdi>
                </dd>
                <dt className="text-text-secondary">Size</dt>
                <dd>
                  <ProductSize product={selectedProduct} className="text-body-regular" />
                </dd>
              </dl>
            </div>
            {selectedProduct.url ? (
              <ButtonLink
                href={selectedProduct.url}
                target="_blank"
                rel="noopener noreferrer"
                variant="secondary"
                size="small"
                trailingIcon={RiExternalLinkLine}
                className="self-start"
              >
                View product page
                <span className="sr-only"> (opens in a new tab)</span>
              </ButtonLink>
            ) : null}
          </Section>
        ) : null}

        {owned && purchase ? (
          <Section title="Purchase">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-body-regular">
              <dt className="text-text-secondary">Product</dt>
              <dd className="text-text-primary">{ownedProduct?.title ?? "Unknown product"}</dd>
              <dt className="text-text-secondary">Store</dt>
              <dd className="text-text-primary">{ownedStore?.name ?? "Unknown store"}</dd>
              <dt className="text-text-secondary">Price</dt>
              <dd className="text-text-primary">{ownedProduct ? <bdi>{formatPrice(ownedProduct.price)}</bdi> : "-"}</dd>
              <dt className="text-text-secondary">Order</dt>
              <dd className="text-text-primary">
                <bdi>{purchase.orderName}</bdi>
              </dd>
              <dt className="text-text-secondary">Purchased</dt>
              <dd className="text-text-primary">{formatRelativeTime(purchase.purchasedAt)}</dd>
            </dl>
          </Section>
        ) : null}
      </DrawerBody>
      <DrawerFooter className="flex-wrap">
        {!isNew ? (
          <Button variant="ghost" className="sm:me-auto" onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        ) : null}
        <Button variant="ghost" onClick={() => onFinish({ discard: isNew })}>
          Cancel
        </Button>
        {!owned ? (
          <Button
            variant="secondary"
            onClick={() => {
              if (!save()) return;
              search.search(object.id);
              onFinish();
            }}
          >
            Save &amp; search
          </Button>
        ) : null}
        <Button
          variant="primary"
          onClick={() => {
            if (save()) onFinish();
          }}
        >
          Save
        </Button>
      </DrawerFooter>
      <AlertDialog
        isOpen={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${object.name}?`}
        description="This removes the object from all spaces and the cart. This can't be undone."
        confirmLabel="Delete"
        onConfirm={() => onFinish({ discard: true })}
      />
    </>
  );
}

/** Mount once in the app layout; driven by the `?object=` URL param. */
export function ObjectDetailsDrawer() {
  const state = useAppState();
  const actions = useAppActions();
  const drawer = useObjectDrawer();
  const { objectId, isNew, close } = drawer;
  const object = objectId ? getObject(state, objectId) : undefined;

  // The id we are already closing ourselves, so the unknown-id effect does not close twice.
  const closingId = useRef<string | null>(null);

  // Keep the last valid snapshot so the exit animation still has content.
  const [shown, setShown] = useState<{ object: SpaceObject; isNew: boolean } | null>(null);
  if (object && (shown?.object.id !== object.id || shown.isNew !== isNew)) {
    setShown({ object, isNew });
  }

  useEffect(() => {
    if (objectId && !object && closingId.current !== objectId) close();
  }, [objectId, object, close]);

  const finish = (opts?: { discard?: boolean }) => {
    if (!objectId) return;
    closingId.current = objectId;
    if (opts?.discard) actions.deleteObject(objectId);
    close();
  };

  const open = Boolean(objectId && object);

  return (
    <Drawer isOpen={open} onOpenChange={(next) => !next && finish({ discard: isNew })} aria-label="Object details">
      {shown ? <ObjectForm key={shown.object.id} object={shown.object} isNew={shown.isNew} onFinish={finish} /> : null}
    </Drawer>
  );
}
