import { OWNED_PRODUCTS, searchCatalogSync } from "@/mocks/catalog";
import type { AppState, CartLine, SpaceObject } from "@/mocks/types";

function minutesAgo(now: number, minutes: number): string {
  return new Date(now - minutes * 60_000).toISOString();
}

type ObjectInit = Omit<SpaceObject, "similarItems" | "source" | "candidateIds" | "search" | "createdAt"> &
  Partial<Pick<SpaceObject, "similarItems">>;

function makeObject(init: ObjectInit, createdAt: string, searched: boolean): SpaceObject {
  const base: SpaceObject = {
    similarItems: [],
    source: "search",
    candidateIds: [],
    search: { status: "idle" },
    createdAt,
    ...init,
  };
  if (!searched) return base;
  const candidateIds = searchCatalogSync(base);
  return {
    ...base,
    candidateIds,
    selectedProductId: candidateIds[0],
    search: { status: candidateIds.length > 0 ? "done" : "empty", lastRunAt: createdAt },
  };
}

export function createSeedState(): AppState {
  const now = Date.now();
  const created = minutesAgo(now, 60 * 24);
  const searchedAt = minutesAgo(now, 60 * 20);

  const sofa = makeObject(
    {
      id: "obj_sofa",
      name: "Sofa",
      dims: { x: 84, y: 36, z: 34 },
      type: "furniture",
      material: "fabric",
      style: "mid-century",
      priceLimit: 1500,
      similarItems: ["couch", "loveseat"],
    },
    searchedAt,
    true,
  );
  const coffeeTable = makeObject(
    {
      id: "obj_coffee_table",
      name: "Coffee table",
      dims: { x: 48, y: 24, z: 18 },
      type: "furniture",
      material: "wood",
      similarItems: ["cocktail table"],
    },
    searchedAt,
    true,
  );
  const floorLamp = makeObject(
    {
      id: "obj_floor_lamp",
      name: "Floor lamp",
      dims: { x: 14, y: 14, z: 64 },
      type: "lighting",
      material: "metal",
      similarItems: ["standing lamp"],
    },
    created,
    false,
  );
  const rug = makeObject(
    { id: "obj_rug", name: "Area rug", dims: { x: 96, y: 120 }, type: "rug", material: "fabric" },
    created,
    false,
  );
  const bookends = makeObject(
    {
      id: "obj_bookends",
      name: "Bookends",
      dims: { x: 6, y: 5, z: 7 },
      type: "decor",
      material: "metal",
    },
    created,
    false,
  );
  const plantPot = makeObject(
    {
      id: "obj_plant_pot",
      name: "Small plant pot",
      dims: { x: 6, y: 6, z: 7 },
      type: "decor",
      material: "ceramic",
      similarItems: ["planter"],
    },
    created,
    false,
  );
  const deskChair = makeObject(
    {
      id: "obj_desk_chair",
      name: "Desk chair",
      dims: { x: 26, y: 26, z: 40 },
      type: "furniture",
      priceLimit: 350,
      similarItems: ["office chair"],
    },
    searchedAt,
    true,
  );
  const poster = makeObject(
    {
      id: "obj_poster",
      name: "Gallery poster",
      dims: { x: 24, y: 1, z: 36 },
      type: "wall-art",
      similarItems: ["framed print"],
    },
    created,
    false,
  );
  const tableLamp = makeObject(
    {
      id: "obj_table_lamp",
      name: "Table lamp",
      dims: { x: 14, y: 14, z: 24 },
      type: "lighting",
    },
    created,
    false,
  );

  const cart: CartLine[] = [sofa, coffeeTable].flatMap((o): CartLine[] =>
    o.selectedProductId
      ? [{ id: `line_${o.id}`, productId: o.selectedProductId, objectId: o.id, qty: 1 }]
      : [],
  );

  return {
    version: 1,
    spaces: [
      {
        id: "space_living",
        name: "Living room",
        type: "floor",
        unit: "in",
        dims: { x: 144, y: 180, z: 96 },
        placements: [
          { objectId: sofa.id, x: 6, y: 8 },
          { objectId: floorLamp.id, x: 120, y: 8 },
          { objectId: rug.id, x: 0, y: 50 },
          { objectId: coffeeTable.id, x: 96, y: 80 },
        ],
        createdAt: created,
        updatedAt: created,
      },
      {
        id: "space_bookshelf",
        name: "Bookshelf",
        type: "shelf",
        unit: "in",
        dims: { x: 36, y: 12, z: 72 },
        weightLimit: 40,
        placements: [
          { objectId: bookends.id, x: 2, y: 3 },
          { objectId: plantPot.id, x: 12, y: 3 },
        ],
        createdAt: created,
        updatedAt: created,
      },
    ],
    objects: [sofa, coffeeTable, floorLamp, rug, bookends, plantPot, deskChair, poster, tableLamp],
    cart,
    checkoutOpened: {},
  };
}

/** Builds the objects created by "Import from Shopify" from the owned products. */
export function createImportedObjects(now: string): SpaceObject[] {
  return OWNED_PRODUCTS.map((p, i) => ({
    id: `obj_imported_${i + 1}`,
    name: p.title,
    dims: { ...p.dims },
    weight: p.weight,
    type: p.type,
    material: p.material,
    style: p.style,
    similarItems: [],
    source: "imported" as const,
    owned: {
      productId: p.id,
      orderName: `#${1040 + i * 17}`,
      purchasedAt: new Date(new Date(now).getTime() - (30 + i * 45) * 86_400_000).toISOString(),
    },
    candidateIds: [],
    search: { status: "idle" as const },
    createdAt: now,
  }));
}
