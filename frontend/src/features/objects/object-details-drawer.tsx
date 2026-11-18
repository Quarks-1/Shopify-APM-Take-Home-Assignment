import { useEffect, useId, useRef, useState } from "react";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiExternalLinkLine,
  RiSparkling2Line,
} from "@remixicon/react";
import { AgentThinking } from "@/components/application/agent-thinking/agent-thinking";
import { Chip } from "@/components/base/badges/chip";
import { Button, ButtonLink } from "@/components/base/buttons/button";
import { Checkbox } from "@/components/base/checkbox/checkbox";
import { Drawer, DrawerBody, DrawerFooter, DrawerHeader } from "@/components/base/drawer/drawer";
import { Input } from "@/components/base/input/input";
import { AlertDialog } from "@/components/base/modal/modal";
import { NumberField } from "@/components/base/number-field/number-field";
import { Select, SelectItem } from "@/components/base/select/select";
import { Tag, TagGroup } from "@/components/base/tag-group/tag-group";
import { Textarea } from "@/components/base/textarea/textarea";
import { describeError, isAbortError, parseObjectPrompt } from "@/lib/api";
import {
  formatDims,
  formatPrice,
  formatRelativeTime,
  fromDisplayLengthKeeping,
  toDisplayLength,
} from "@/lib/format";
import {
  MATERIAL_OPTIONS,
  OBJECT_TYPE_OPTIONS,
  STYLE_OPTIONS,
  type LengthUnit,
  type ObjectDims,
  type ObjectType,
  type SpaceObject,
} from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import {
  footprint,
  getObject,
  getParentSpaces,
  getProduct,
  getSelectedProduct,
  getStore,
} from "@/state/selectors";
import { cx } from "@/utils/cx";
import { PinterestReferencePicker, type ParsedPinGroups } from "./pinterest-reference-picker";
import { PriceRangeSlider, type PriceRange } from "./price-range-slider";
import { ProductRatingText, ProductSize } from "./product-card";
import { ProductTile } from "./product-tile";
import { useObjectDrawer } from "./use-object-drawer";
import { useObjectSearch } from "./use-object-search";

const ANY = "any";
const PLACEHOLDER_NEW_NAME = "New object";
const MAX_NAME_LENGTH = 120;
const MAX_SIMILAR_ITEMS = 20;
const MAX_SIMILAR_LENGTH = 80;

const MIN_RATING_STEPS = [3, 3.5, 4, 4.5];
const formatMinRating = (value: number) => `${value}+ stars`;

function parseOption<T extends string>(options: { value: T }[], key: string | number | null): T | undefined {
  return options.find((o) => o.value === key)?.value;
}

/** NumberField reports an empty value as NaN. */
function numberOrUndefined(value: number): number | undefined {
  return Number.isNaN(value) ? undefined : value;
}

function formatPriceSummary(range: PriceRange): string | null {
  const minTxt =
    range.min === undefined ? null : `${range.minFuzzy ? "~" : ""}${formatPrice(range.min)}`;
  const maxTxt =
    range.max === undefined ? null : `${range.maxFuzzy ? "~" : ""}${formatPrice(range.max)}`;
  if (minTxt && maxTxt) return `${minTxt} – ${maxTxt}`;
  if (maxTxt) return range.maxFuzzy ? `Around ${formatPrice(range.max!)}` : `Under ${maxTxt}`;
  if (minTxt) return range.minFuzzy ? `Around ${formatPrice(range.min!)}+` : `Over ${minTxt}`;
  return null;
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
  isDrawn: boolean;
  /** Marks a new object as committed so closing/navigating away won't discard it. */
  onCommit: () => void;
  /** Close the drawer; with `discard` the object is deleted first. */
  onFinish: (opts?: { discard?: boolean }) => void;
}

function ObjectForm({ object, isNew, isDrawn, onCommit, onFinish }: ObjectFormProps) {
  const state = useAppState();
  const actions = useAppActions();
  const search = useObjectSearch();
  const advancedPanelId = useId();

  const initialSpaceIds = getParentSpaces(state, object.id).map((s) => s.id);
  const [spaceIds, setSpaceIds] = useState(initialSpaceIds);

  const parentSpace = state.spaces.find((s) => spaceIds.includes(s.id));
  const unit: LengthUnit = parentSpace?.unit ?? "in";
  const owned = object.source === "imported";
  const purchase = object.owned;
  const ownedProduct = purchase ? getProduct(state, purchase.productId) : undefined;
  const ownedStore = ownedProduct ? getStore(state, ownedProduct.storeId) : undefined;
  // The live object: a search may finish (and pick a product) while the drawer is open.
  const selectedProduct = owned ? undefined : getSelectedProduct(state, getObject(state, object.id) ?? object);
  const selectedStore = selectedProduct ? getStore(state, selectedProduct.storeId) : undefined;

  const hasPrefilledName = object.name.trim() !== "" && object.name !== PLACEHOLDER_NEW_NAME;

  const [displayUnit, setDisplayUnit] = useState<LengthUnit>(unit);
  const [promptText, setPromptText] = useState(() =>
    isNew ? (object.description ?? (hasPrefilledName ? object.name : "")) : (object.description ?? ""),
  );
  const [name, setName] = useState(() => (isNew && !hasPrefilledName ? "" : object.name));
  const [description, setDescription] = useState(object.description ?? "");
  const [type, setType] = useState<ObjectType>(object.type);
  const [baseDims, setBaseDims] = useState<ObjectDims>(object.dims);
  const [x, setX] = useState(toDisplayLength(object.dims.x, unit));
  const [y, setY] = useState(object.dims.y === undefined ? NaN : toDisplayLength(object.dims.y, unit));
  const [z, setZ] = useState(object.dims.z === undefined ? NaN : toDisplayLength(object.dims.z, unit));
  const [weight, setWeight] = useState(object.weight ?? NaN);
  const [material, setMaterial] = useState<string>(object.material ?? ANY);
  const [style, setStyle] = useState<string>(object.style ?? ANY);
  const [priceRange, setPriceRange] = useState<PriceRange>({
    min: object.priceMin,
    max: object.priceLimit,
    minFuzzy: object.priceMinFuzzy,
    maxFuzzy: object.priceLimitFuzzy,
  });
  const [minRating, setMinRating] = useState<string>(object.minRating === undefined ? ANY : String(object.minRating));
  const [similar, setSimilar] = useState(object.similarItems);
  const [similarDraft, setSimilarDraft] = useState("");
  const [similarAttempted, setSimilarAttempted] = useState(false);
  const [referencePins, setReferencePins] = useState<string[]>(object.referencePins ?? []);
  const [submitted, setSubmitted] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // New-object AI prompt & disclosure state
  const [advancedOpen, setAdvancedOpen] = useState(!isNew);
  const [advancedTouched, setAdvancedTouched] = useState(!isNew || hasPrefilledName);
  const [hasGenerated, setHasGenerated] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parsedPins, setParsedPins] = useState<ParsedPinGroups | null>(null);
  const parseAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      parseAbortRef.current?.abort();
    };
  }, []);

  if (unit !== displayUnit) {
    const convertDim = (val: number, stored: number | undefined): number => {
      if (Number.isNaN(val)) return NaN;
      return toDisplayLength(fromDisplayLengthKeeping(val, displayUnit, stored), unit);
    };
    setDisplayUnit(unit);
    setX(convertDim(x, baseDims.x));
    setY(convertDim(y, baseDims.y));
    setZ(convertDim(z, baseDims.z));
  }

  const isWall = parentSpace?.type === "wall" || type === "wall-art";

  const minRatingOptions =
    object.minRating === undefined || MIN_RATING_STEPS.includes(object.minRating)
      ? MIN_RATING_STEPS
      : [...MIN_RATING_STEPS, object.minRating].sort((a, b) => a - b);

  const trimmedName = name.trim();
  const xInches = x > 0 ? fromDisplayLengthKeeping(x, unit, baseDims.x) : 0;
  const yInches = !Number.isNaN(y) && y > 0 ? fromDisplayLengthKeeping(y, unit, baseDims.y) : 0;
  const zInches = !Number.isNaN(z) && z > 0 ? fromDisplayLengthKeeping(z, unit, baseDims.z) : 0;

  const draftTrimmed = similarDraft.trim();
  const isDuplicateDraft =
    draftTrimmed !== "" && similar.some((s) => s.toLowerCase() === draftTrimmed.toLowerCase());
  const similarValidationError =
    draftTrimmed.length > MAX_SIMILAR_LENGTH || similar.some((s) => s.trim().length > MAX_SIMILAR_LENGTH)
      ? `Each item must be ${MAX_SIMILAR_LENGTH} characters or fewer`
      : similar.length > MAX_SIMILAR_ITEMS ||
          (draftTrimmed !== "" && !isDuplicateDraft && similar.length >= MAX_SIMILAR_ITEMS)
        ? `You can add up to ${MAX_SIMILAR_ITEMS} similar items`
        : undefined;

  const similarHintError =
    draftTrimmed.length > MAX_SIMILAR_LENGTH
      ? `Each item must be ${MAX_SIMILAR_LENGTH} characters or fewer`
      : submitted
        ? similarValidationError
        : similarAttempted
          ? (similarValidationError ??
            (similar.length >= MAX_SIMILAR_ITEMS ? `You can add up to ${MAX_SIMILAR_ITEMS} similar items` : undefined))
          : undefined;

  const errors = {
    name: !trimmedName
      ? "Name is required"
      : trimmedName.length > MAX_NAME_LENGTH
        ? `Name must be ${MAX_NAME_LENGTH} characters or fewer`
        : undefined,
    x: x > 0 && xInches > 0 ? undefined : "Width must be greater than 0",
    y: isWall
      ? Number.isNaN(y) || (y > 0 && yInches > 0)
        ? undefined
        : "Depth must be greater than 0"
      : y > 0 && yInches > 0
        ? undefined
        : "Depth must be greater than 0",
    z: !isWall
      ? Number.isNaN(z) || (z > 0 && zInches > 0)
        ? undefined
        : "Height must be greater than 0"
      : z > 0 && zInches > 0
        ? undefined
        : "Height must be greater than 0",
    weight: Number.isNaN(weight) || weight > 0 ? undefined : "Weight must be greater than 0",
    similar: similarValidationError,
  };
  const invalid = Boolean(errors.name || errors.x || errors.y || errors.z || errors.weight || errors.similar);

  const handleGenerate = async () => {
    const trimmed = promptText.trim();
    if (!trimmed || parsing) return;
    parseAbortRef.current?.abort();
    const controller = new AbortController();
    parseAbortRef.current = controller;
    setParsing(true);
    setParseError(null);

    try {
      const response = await parseObjectPrompt(
        {
          prompt: trimmed,
          spaceIds,
          drawnDims: isDrawn ? object.dims : null,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;

      const { parsed, similarPins, categoryPins } = response;
      setName(parsed.name);
      setDescription(parsed.description);
      setType(parsed.type);
      setBaseDims(parsed.dims);
      setX(toDisplayLength(parsed.dims.x, unit));
      setY(parsed.dims.y === undefined ? NaN : toDisplayLength(parsed.dims.y, unit));
      setZ(parsed.dims.z === undefined ? NaN : toDisplayLength(parsed.dims.z, unit));
      setWeight(parsed.weight ?? NaN);
      setMaterial(parsed.material ?? ANY);
      setStyle(parsed.style ?? ANY);
      setPriceRange({
        min: parsed.priceMin ?? undefined,
        max: parsed.priceLimit ?? undefined,
        minFuzzy: parsed.priceMinFuzzy,
        maxFuzzy: parsed.priceLimitFuzzy,
      });
      setMinRating(parsed.minRating === null ? ANY : String(parsed.minRating));
      setSimilar(parsed.similarItems);
      setSimilarDraft("");
      setSimilarAttempted(false);
      setParsedPins({ similar: similarPins, category: categoryPins });
      setHasGenerated(true);
      setSubmitted(false);
    } catch (err) {
      if (isAbortError(err)) return;
      setParseError(describeError(err));
    } finally {
      if (parseAbortRef.current === controller) {
        parseAbortRef.current = null;
        setParsing(false);
      }
    }
  };

  const cancelGenerate = () => {
    parseAbortRef.current?.abort();
    parseAbortRef.current = null;
    setParsing(false);
  };

  const toggleAdvanced = () => {
    setAdvancedOpen((prev) => {
      const next = !prev;
      if (next) setAdvancedTouched(true);
      return next;
    });
  };

  const addSimilar = () => {
    const value = similarDraft.trim();
    if (!value) {
      if (similar.length >= MAX_SIMILAR_ITEMS) setSimilarAttempted(true);
      return;
    }
    if (value.length > MAX_SIMILAR_LENGTH) {
      setSimilarAttempted(true);
      return;
    }
    if (!similar.some((s) => s.toLowerCase() === value.toLowerCase()) && similar.length >= MAX_SIMILAR_ITEMS) {
      setSimilarAttempted(true);
      return;
    }
    setSimilar((prev) => (prev.some((s) => s.toLowerCase() === value.toLowerCase()) ? prev : [...prev, value]));
    setSimilarDraft("");
    setSimilarAttempted(false);
  };

  /** Persists the form; returns false when validation fails. */
  const save = (): boolean => {
    setSubmitted(true);
    if (invalid) {
      if (isNew && !advancedOpen) setAdvancedOpen(true);
      return false;
    }
    const nextSimilar = draftTrimmed && !isDuplicateDraft ? [...similar, draftTrimmed] : similar;
    if (nextSimilar !== similar) setSimilar(nextSimilar);
    if (draftTrimmed) setSimilarDraft("");
    const nextDims: ObjectDims = {
      x: xInches,
      y: Number.isNaN(y) ? undefined : yInches,
      z: Number.isNaN(z) ? undefined : zInches,
    };
    const cleanedDesc = description.trim();
    actions.updateObject(object.id, {
      name: trimmedName,
      description: cleanedDesc || undefined,
      type,
      dims: nextDims,
      weight: numberOrUndefined(weight),
      material: parseOption(MATERIAL_OPTIONS, material),
      style: parseOption(STYLE_OPTIONS, style),
      priceMin: priceRange.min,
      priceLimit: priceRange.max,
      priceMinFuzzy: priceRange.minFuzzy,
      priceLimitFuzzy: priceRange.maxFuzzy,
      minRating: minRating === ANY ? undefined : Number(minRating),
      similarItems: nextSimilar,
      referencePins,
    });
    for (const space of state.spaces) {
      const was = initialSpaceIds.includes(space.id);
      const want = spaceIds.includes(space.id);
      if (want && !was) {
        actions.placeObject(space.id, object.id);
      } else if (!want && was) {
        actions.removeFromSpace(space.id, object.id);
      } else if (want && was && isNew && !isDrawn) {
        const placement = space.placements.find((p) => p.objectId === object.id);
        if (placement) {
          const fp = footprint({ ...object, dims: nextDims }, space);
          const clampedX = Math.max(0, Math.min(placement.x, Math.max(0, space.dims.x - fp.w)));
          const clampedY = Math.max(0, Math.min(placement.y, Math.max(0, space.dims.y - fp.h)));
          if (clampedX !== placement.x || clampedY !== placement.y) {
            actions.moveObject(space.id, object.id, { x: clampedX, y: clampedY });
          }
        }
      }
    }
    onCommit();
    return true;
  };

  const showError = (message: string | undefined) => (submitted ? message : undefined);

  const typeLabel = OBJECT_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type;
  const materialLabel = MATERIAL_OPTIONS.find((o) => o.value === material)?.label;
  const styleLabel = STYLE_OPTIONS.find((o) => o.value === style)?.label;
  const priceLabel = formatPriceSummary(priceRange);
  const previewDims =
    xInches > 0
      ? formatDims(
          {
            x: xInches,
            y: Number.isNaN(y) ? undefined : yInches,
            z: Number.isNaN(z) ? undefined : zInches,
          },
          unit,
        )
      : "";

  const showFeaturedPinterest =
    isNew &&
    hasGenerated &&
    parsedPins !== null &&
    (parsedPins.similar.length > 0 || parsedPins.category.length > 0 || referencePins.length > 0);

  const saveDisabled = parsing || (isNew && !hasGenerated && !advancedTouched);

  return (
    <>
      <DrawerHeader title={isNew ? "New object" : object.name} />
      <DrawerBody className="flex flex-col gap-5 py-4">
        {isNew ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2.5">
              <Textarea
                label="Describe your object"
                placeholder="e.g. Japandi style white fabric couch with bouclé cushioning"
                rows={3}
                autoResize
                maxRows={6}
                maxLength={500}
                showCount
                autoFocus
                isDisabled={parsing}
                value={promptText}
                onChange={(next) => {
                  setPromptText(next);
                  if (parseError) setParseError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    void handleGenerate();
                  }
                }}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                {parsing ? (
                  <div className="flex w-full items-center justify-between gap-2 py-0.5">
                    <AgentThinking variant="stars" label="Filling out object details" />
                    <Button variant="ghost" size="small" onClick={cancelGenerate}>
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <>
                    <Button
                      variant="primary"
                      size="small"
                      leadingIcon={RiSparkling2Line}
                      disabled={!promptText.trim()}
                      onClick={() => void handleGenerate()}
                    >
                      {hasGenerated ? "Update from description" : "Generate details"}
                    </Button>
                    <span className="hidden text-caption-1-regular text-text-tertiary sm:inline">
                      Press ⌘ + Enter to generate
                    </span>
                  </>
                )}
              </div>
              {parseError ? (
                <p role="alert" className="text-body-2-regular text-text-error-primary">
                  Couldn&apos;t fill details automatically: {parseError}. You can try again or use advanced settings below.
                </p>
              ) : null}
            </div>

            {hasGenerated ? (
              <div className="flex flex-col gap-2.5 rounded-3xl border border-border-button-default bg-background-secondary-default p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-caption-1-medium text-text-secondary">Generated object</span>
                    <p className="truncate text-headline-medium text-text-primary">{name || "Untitled object"}</p>
                  </div>
                  <Chip variant="caption" color="lime">
                    Ready
                  </Chip>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Chip variant="caption" color="gray">
                    {typeLabel}
                  </Chip>
                  {previewDims ? (
                    <Chip variant="caption" color="neutral">
                      {previewDims}
                    </Chip>
                  ) : null}
                  {materialLabel ? (
                    <Chip variant="caption" color="neutral">
                      {materialLabel}
                    </Chip>
                  ) : null}
                  {styleLabel ? (
                    <Chip variant="caption" color="neutral">
                      {styleLabel}
                    </Chip>
                  ) : null}
                  {priceLabel ? (
                    <Chip variant="caption" color="neutral">
                      {priceLabel}
                    </Chip>
                  ) : null}
                  {minRating !== ANY ? (
                    <Chip variant="caption" color="neutral">
                      {formatMinRating(Number(minRating))}
                    </Chip>
                  ) : null}
                  {!Number.isNaN(weight) && weight > 0 ? (
                    <Chip variant="caption" color="neutral">
                      Max {weight} lb
                    </Chip>
                  ) : null}
                </div>
              </div>
            ) : null}

            {showFeaturedPinterest ? (
              <PinterestReferencePicker
                variant="featured"
                spaceIds={spaceIds}
                objectType={type}
                selectedPinIds={referencePins}
                onChange={setReferencePins}
                parsedPins={parsedPins}
              />
            ) : null}

            <button
              type="button"
              aria-expanded={advancedOpen}
              aria-controls={advancedPanelId}
              onClick={toggleAdvanced}
              className={cx(
                "flex w-full items-center justify-between gap-3 rounded-2xl border border-border-button-default bg-background-primary-default px-3.5 py-2.5 text-start transition-colors",
                "hover:border-border-button-hover hover:bg-background-secondary-default",
                "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
              )}
            >
              <span className="text-body-medium text-text-primary">
                {advancedOpen ? "Hide advanced settings" : "Show advanced settings"}
              </span>
              <span className="inline-flex items-center gap-2">
                <span className="text-caption-1-medium text-text-tertiary">
                  {hasGenerated ? "Preview & edit" : "Type, size & filters"}
                </span>
                <RiArrowDownSLine
                  aria-hidden
                  className={cx(
                    "size-4 text-foreground-icon-secondary transition-transform duration-150",
                    advancedOpen && "rotate-180",
                  )}
                />
              </span>
            </button>
          </div>
        ) : null}

        {advancedOpen ? (
          <div id={advancedPanelId} className="flex flex-col gap-5">
            <Section title="Basics">
              <Input
                label="Name"
                placeholder="e.g. Japandi white bouclé couch"
                value={name}
                onChange={setName}
                isRequired
                isInvalid={Boolean(showError(errors.name) || trimmedName.length > MAX_NAME_LENGTH)}
                errorMessage={errors.name}
                autoFocus={!isNew}
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
              <Textarea
                label="Description"
                placeholder="Optional details about color, texture, silhouette, or materials"
                rows={2}
                autoResize
                maxRows={5}
                maxLength={500}
                value={description}
                onChange={setDescription}
                hint="Optional — steers catalog search without forcing rigid filters"
              />
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
                  label={`Depth (${unit})`}
                  value={y}
                  onChange={setY}
                  minValue={0}
                  isRequired={!isWall}
                  isInvalid={Boolean(showError(errors.y))}
                  hint={showError(errors.y) ?? (isWall ? "Optional" : undefined)}
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
              </div>
              <NumberField
                label="Weight (lb)"
                value={weight}
                onChange={setWeight}
                minValue={0}
                isInvalid={Boolean(showError(errors.weight))}
                hint={showError(errors.weight) ?? "Optional"}
              />
            </Section>

            <Section title="Style">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Select
                  label="Material"
                  className="w-full"
                  selectedKey={material}
                  onSelectionChange={(k) => setMaterial(String(k))}
                >
                  <SelectItem id={ANY}>Any</SelectItem>
                  {MATERIAL_OPTIONS.map((o) => (
                    <SelectItem key={o.value} id={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </Select>
                <Select
                  label="Style"
                  className="w-full"
                  selectedKey={style}
                  onSelectionChange={(k) => setStyle(String(k))}
                >
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

            <Section title="Minimum rating">
              <Select
                label="Minimum star rating"
                className="w-full"
                selectedKey={minRating}
                onSelectionChange={(k) => setMinRating(String(k))}
              >
                <SelectItem id={ANY}>Any</SelectItem>
                {minRatingOptions.map((value) => (
                  <SelectItem key={value} id={String(value)}>
                    {formatMinRating(value)}
                  </SelectItem>
                ))}
              </Select>
            </Section>

            <Section title="Similar items">
              <TagGroup
                aria-label="Similar items"
                items={similar.map((s) => ({ id: s }))}
                emptyState="No similar items yet"
                onRemove={(keys) => {
                  setSimilar((prev) => prev.filter((s) => !keys.has(s)));
                  if (similarAttempted) setSimilarAttempted(false);
                }}
              >
                {(item) => <Tag id={item.id}>{item.id}</Tag>}
              </TagGroup>
              <div className="flex items-start gap-2">
                <Input
                  aria-label="Add a similar item"
                  placeholder="e.g. Eames lounge chair"
                  className="min-w-0 flex-1"
                  value={similarDraft}
                  onChange={(value) => {
                    setSimilarDraft(value);
                    if (similarAttempted) setSimilarAttempted(false);
                  }}
                  isInvalid={Boolean(similarHintError)}
                  errorMessage={similarHintError}
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
              {!showFeaturedPinterest ? (
                <div className="pt-2">
                  <PinterestReferencePicker
                    variant="inline"
                    spaceIds={spaceIds}
                    objectType={type}
                    selectedPinIds={referencePins}
                    onChange={setReferencePins}
                    parsedPins={parsedPins}
                  />
                </div>
              ) : null}
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
                    {selectedProduct.rating ? (
                      <>
                        <dt className="text-text-secondary">Rating</dt>
                        <dd className="text-text-primary">
                          <ProductRatingText rating={selectedProduct.rating} />
                        </dd>
                      </>
                    ) : null}
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
                  <dd className="text-text-primary">
                    {ownedProduct ? <bdi>{formatPrice(ownedProduct.price)}</bdi> : "-"}
                  </dd>
                  <dt className="text-text-secondary">Order</dt>
                  <dd className="text-text-primary">
                    <bdi>{purchase.orderName}</bdi>
                  </dd>
                  <dt className="text-text-secondary">Purchased</dt>
                  <dd className="text-text-primary">{formatRelativeTime(purchase.purchasedAt)}</dd>
                </dl>
              </Section>
            ) : null}
          </div>
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
            disabled={saveDisabled}
            onClick={() => {
              if (!save()) return;
              search.search(object.id, { force: true });
              onFinish();
            }}
          >
            Save &amp; search
          </Button>
        ) : null}
        <Button
          variant="primary"
          disabled={saveDisabled}
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
  const { objectId, isNew, isDrawn, close } = drawer;
  const object = objectId ? getObject(state, objectId) : undefined;
  const open = Boolean(objectId && object);

  // The id we are already closing ourselves, so the unknown-id effect does not close twice.
  const closingId = useRef<string | null>(null);
  // Tracks uncommitted `&new=1` objects so Browser Back / Forward cleans up abandoned "New object" rows.
  const uncommittedNewIdRef = useRef<string | null>(null);
  // Newly created objects that have been explicitly saved, so async URL close transitions never discard them.
  const committedNewIdsRef = useRef(new Set<string>());

  const [prevOpenState, setPrevOpenState] = useState<{
    open: boolean;
    objectId: string | null;
    isNew: boolean;
    isDrawn: boolean;
  }>({ open: false, objectId: null, isNew: false, isDrawn: false });

  // Keep the last valid snapshot so the exit animation still has content; bump session on each open/id change.
  const [shown, setShown] = useState<{
    object: SpaceObject;
    isNew: boolean;
    isDrawn: boolean;
    session: number;
  } | null>(null);
  if (
    open !== prevOpenState.open ||
    objectId !== prevOpenState.objectId ||
    isNew !== prevOpenState.isNew ||
    isDrawn !== prevOpenState.isDrawn
  ) {
    setPrevOpenState({ open, objectId, isNew, isDrawn });
    if (open && object) {
      const nextSession =
        !prevOpenState.open || prevOpenState.objectId !== object.id || prevOpenState.isNew !== isNew
          ? (shown?.session ?? 0) + 1
          : (shown?.session ?? 1);
      setShown({ object, isNew, isDrawn, session: nextSession });
    }
  }

  useEffect(() => {
    const abandonedId = uncommittedNewIdRef.current;
    if (abandonedId && (objectId !== abandonedId || !isNew)) {
      uncommittedNewIdRef.current = null;
      if (!committedNewIdsRef.current.has(abandonedId)) {
        actions.deleteObject(abandonedId);
      }
    }
    if (objectId && isNew && !committedNewIdsRef.current.has(objectId)) {
      uncommittedNewIdRef.current = objectId;
    }
  }, [objectId, isNew, actions]);

  useEffect(() => {
    if (objectId && !object && closingId.current !== objectId) close();
    if (!objectId) closingId.current = null;
  }, [objectId, object, close]);

  const commitNew = () => {
    if (objectId) committedNewIdsRef.current.add(objectId);
    if (shown?.object.id) committedNewIdsRef.current.add(shown.object.id);
    uncommittedNewIdRef.current = null;
  };

  const finish = (opts?: { discard?: boolean }) => {
    if (!objectId) return;
    closingId.current = objectId;
    if (opts?.discard) {
      if (uncommittedNewIdRef.current === objectId) {
        uncommittedNewIdRef.current = null;
      }
      actions.deleteObject(objectId);
    }
    close();
  };

  return (
    <Drawer isOpen={open} onOpenChange={(next) => !next && finish({ discard: isNew })} aria-label="Object details">
      {shown ? (
        <ObjectForm
          key={`${shown.object.id}:${shown.session}`}
          object={shown.object}
          isNew={shown.isNew}
          isDrawn={shown.isDrawn}
          onCommit={commitNew}
          onFinish={finish}
        />
      ) : null}
    </Drawer>
  );
}
