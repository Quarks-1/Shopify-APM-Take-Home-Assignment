// Mock data for "Import from Shopify": the products the user already owns and
// their stores. There is no real order history; search results come from the
// backend's searcher.
import type { Dims, Material, ObjectType, Product, Store, Style } from "@/mocks/types";

export const STORES: Store[] = [
  { id: "store_oak", name: "Oak & Iron", domain: "oak-and-iron.myshopify.com" },
  { id: "store_nordlys", name: "Nordlys Home", domain: "nordlys-home.myshopify.com" },
  { id: "store_brightwick", name: "Brightwick Studio", domain: "brightwick-studio.myshopify.com" },
  { id: "store_plinth", name: "Plinth & Pine", domain: "plinth-and-pine.myshopify.com" },
];

const OAK = "store_oak";
const NORD = "store_nordlys";
const BRIGHT = "store_brightwick";
const PLINTH = "store_plinth";

/** Mock catalog products always know their full size, material and style. */
export type OwnedProduct = Product & { dims: Dims; material: Material; style: Style; tags: string[] };

function product(
  id: string,
  title: string,
  storeId: string,
  price: number,
  dims: [number, number, number?],
  weight: number | undefined,
  type: ObjectType,
  material: Material,
  style: Style,
  tags: string[],
): OwnedProduct {
  const d: Dims = dims[2] === undefined ? { x: dims[0], y: dims[1] } : { x: dims[0], y: dims[1], z: dims[2] };
  return { id, title, storeId, price, currency: "USD", dims: d, weight, type, material, style, tags };
}

/** Products the user already owns, used for "imported from Shopify" objects. */
export const OWNED_PRODUCTS: OwnedProduct[] = [
  product("prd_owned_lounge", "Cedar Lounge Chair", NORD, 460, [30, 31, 32], 26, "furniture", "wood", "scandinavian", ["armchair", "chair", "lounge"]),
  product("prd_owned_reading", "Halo Reading Lamp", BRIGHT, 120, [12, 12, 58], 8, "lighting", "metal", "modern", ["floor", "lamp", "reading"]),
  product("prd_owned_cube", "Cube Storage Shelf", OAK, 180, [30, 14, 30], 28, "storage", "wood", "industrial", ["shelf", "cube", "storage"]),
  product("prd_owned_sundial", "Sundial Framed Print", PLINTH, 55, [20, 1, 28], 3, "wall-art", "wood", "mid-century", ["print", "framed", "poster"]),
];

/** A mock owned product. Components should use `getProduct(state, id)` from the selectors. */
export function getOwnedProductById(id: string): OwnedProduct | undefined {
  return OWNED_PRODUCTS.find((p) => p.id === id);
}

/** A mock store of the owned products. Components should use `getStore(state, id)` from the selectors. */
export function getMockStoreById(id: string): Store | undefined {
  return STORES.find((s) => s.id === id);
}
