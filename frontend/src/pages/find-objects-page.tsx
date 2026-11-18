import { useEffect, useState, type FormEvent } from "react";
import { RiAddLine, RiSearchLine, RiShoppingCart2Line } from "@remixicon/react";
import { useNavigate, useSearchParams } from "react-router";
import { Counter } from "@/components/base/badges/counter";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { Select, SelectItem } from "@/components/base/select/select";
import { toast } from "@/components/base/toast/toast";
import { ObjectCarouselList } from "@/features/objects/object-carousel-list";
import { PriceRangeSlider, type PriceRange } from "@/features/objects/price-range-slider";
import { useObjectDrawer } from "@/features/objects/use-object-drawer";
import { useObjectSearch } from "@/features/objects/use-object-search";
import { OBJECT_TYPE_OPTIONS, type ObjectType } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { getSearchableObjects, getUnassignedObjects } from "@/state/selectors";

type Filter = "unassigned" | "all";

const MAX_NAME_LENGTH = 120;

function parseType(key: string | number | null): ObjectType | undefined {
  return OBJECT_TYPE_OPTIONS.find((o) => o.value === key)?.value;
}

export function FindObjectsPage() {
  const state = useAppState();
  const { createObject, addAllToCart } = useAppActions();
  const { search, searchAll } = useObjectSearch();
  const drawer = useObjectDrawer();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const [name, setName] = useState("");
  const [type, setType] = useState<ObjectType>("furniture");
  const [priceRange, setPriceRange] = useState<PriceRange>({});
  const [filter, setFilter] = useState<Filter>("unassigned");
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  const focusParam = params.get("focus");
  useEffect(() => {
    if (!focusParam) return;
    setHighlightedId(focusParam);
    if (state.objects.some((o) => o.id === focusParam) && !getUnassignedObjects(state).some((o) => o.id === focusParam)) {
      setFilter("all");
    }
    const next = new URLSearchParams(params);
    next.delete("focus");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per focus param
  }, [focusParam]);

  const visible = filter === "unassigned" ? getUnassignedObjects(state) : getSearchableObjects(state);
  const visibleIds = visible.map((o) => o.id);
  const canBuyAll = visible.some(
    (o) => o.source === "search" && Boolean(o.selectedProductId) && !state.cart.some((l) => l.objectId === o.id),
  );
  const trimmed = name.trim();
  const nameTooLong = trimmed.length > MAX_NAME_LENGTH;
  const nameError = nameTooLong ? `Name must be ${MAX_NAME_LENGTH} characters or fewer` : undefined;

  const create = (initialName: string): string => {
    const id = createObject({
      name: initialName,
      type,
      priceMin: priceRange.min,
      priceLimit: priceRange.max,
      priceMinFuzzy: priceRange.minFuzzy,
      priceLimitFuzzy: priceRange.maxFuzzy,
    });
    setName("");
    setType("furniture");
    setPriceRange({});
    return id;
  };

  const handleSearch = (event?: FormEvent) => {
    event?.preventDefault();
    if (!trimmed || nameTooLong) return;
    const id = create(trimmed);
    search(id);
    setHighlightedId(id);
  };

  const handleMoreDetails = () => {
    if (nameTooLong) return;
    const id = create(trimmed || "New object");
    drawer.open(id, { isNew: true });
  };

  const handleNewObject = () => {
    const id = createObject({ name: "New object", type: "furniture" });
    drawer.open(id, { isNew: true });
  };

  const handleBuyAll = () => {
    const added = addAllToCart(visibleIds);
    if (added > 0) {
      toast.success(`Added ${added} ${added === 1 ? "item" : "items"} to cart`);
      navigate("/cart");
    } else {
      toast("Nothing to buy yet", { description: "Run a search and pick a product first." });
    }
  };

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-title-1-medium text-text-primary">Find new objects</h1>
          <p className="text-body-regular text-text-secondary">
            Search the catalog for objects that aren't tied to a space yet.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" leadingIcon={RiAddLine} onClick={handleNewObject}>
            New object
          </Button>
          <Button
            variant="secondary"
            leadingIcon={RiSearchLine}
            disabled={visibleIds.length === 0}
            onClick={() => searchAll(visibleIds)}
          >
            Search all
          </Button>
          <Button leadingIcon={RiShoppingCart2Line} disabled={!canBuyAll} onClick={handleBuyAll}>
            Buy all
          </Button>
        </div>
      </header>

      <form
        onSubmit={handleSearch}
        className="flex flex-wrap items-end gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-4"
      >
        <Input
          label="Object name"
          placeholder="e.g. Walnut side table"
          value={name}
          onChange={setName}
          isInvalid={nameTooLong}
          errorMessage={nameError}
          className="min-w-48 flex-[2_1_14rem]"
        />
        <Select
          label="Type"
          selectedKey={type}
          onSelectionChange={(key) => setType(parseType(key) ?? type)}
          className="min-w-36 flex-[1_1_9rem]"
        >
          {OBJECT_TYPE_OPTIONS.map((o) => (
            <SelectItem key={o.value} id={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </Select>
        <PriceRangeSlider value={priceRange} onChange={setPriceRange} className="min-w-56 flex-[2_1_16rem]" />
        <div className="flex items-center gap-2">
          <Button type="submit" leadingIcon={RiSearchLine} disabled={!trimmed || nameTooLong}>
            Search
          </Button>
          <Button type="button" variant="ghost" disabled={nameTooLong} onClick={handleMoreDetails}>
            More details
          </Button>
        </div>
      </form>

      <section className="flex flex-col gap-4" aria-labelledby="find-results">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 id="find-results" className="text-title-3-semibold text-text-primary">
              Objects
            </h2>
            <Counter>{visible.length}</Counter>
          </div>
          <SegmentedControl
            aria-label="Object filter"
            selectedKeys={[filter]}
            onSelectionChange={(keys) => {
              const next = [...keys][0];
              if (next === "unassigned" || next === "all") setFilter(next);
            }}
          >
            <SegmentedControlItem id="unassigned">Unassigned</SegmentedControlItem>
            <SegmentedControlItem id="all">All objects</SegmentedControlItem>
          </SegmentedControl>
        </div>
        <ObjectCarouselList
          objectIds={visibleIds}
          highlightedId={highlightedId}
          onHighlight={setHighlightedId}
          emptyState={
            <div className="rounded-3xl border border-dashed border-border-button-default p-8 text-center text-body-regular text-text-secondary">
              No unassigned objects. Define one above or switch to All objects.
            </div>
          }
        />
      </section>
    </>
  );
}
