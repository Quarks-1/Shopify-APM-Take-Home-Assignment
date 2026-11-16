import type {
  Dims,
  Material,
  ObjectType,
  Product,
  SearchStep,
  SpaceObject,
  Store,
  Style,
} from "@/mocks/types";

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
): Product {
  const d: Dims = dims[2] === undefined ? { x: dims[0], y: dims[1] } : { x: dims[0], y: dims[1], z: dims[2] };
  return { id, title, storeId, price, currency: "USD", dims: d, weight, type, material, style, tags };
}

export const PRODUCTS: Product[] = [
  // Sofas
  product("prd_sofa_hollis", "Hollis 3-Seat Sofa", NORD, 1290, [84, 36, 33], 110, "furniture", "fabric", "mid-century", ["sofa", "couch", "3-seat"]),
  product("prd_sofa_marlow", "Marlow Loveseat", NORD, 899, [62, 34, 32], 85, "furniture", "fabric", "modern", ["sofa", "couch", "loveseat", "2-seat"]),
  product("prd_sofa_tanner", "Tanner Leather Sofa", PLINTH, 1450, [88, 37, 34], 130, "furniture", "mixed", "vintage", ["sofa", "couch", "leather"]),
  product("prd_sofa_pike", "Pike Sectional Sofa", BRIGHT, 2890, [112, 64, 34], 210, "furniture", "fabric", "modern", ["sofa", "couch", "sectional"]),
  // Armchairs
  product("prd_chair_birch", "Birch Armchair", NORD, 540, [30, 32, 31], 28, "furniture", "wood", "scandinavian", ["armchair", "chair", "lounge"]),
  product("prd_chair_orla", "Orla Accent Armchair", BRIGHT, 420, [28, 30, 32], 24, "furniture", "fabric", "modern", ["armchair", "chair", "accent"]),
  // Coffee tables
  product("prd_coffee_rowan", "Rowan Coffee Table", OAK, 329, [46, 24, 17], 38, "furniture", "wood", "mid-century", ["coffee", "table", "center"]),
  product("prd_coffee_slate", "Slate Coffee Table", BRIGHT, 410, [48, 26, 16], 62, "furniture", "mixed", "brutalist", ["coffee", "table", "stone"]),
  product("prd_coffee_orbit", "Orbit Glass Coffee Table", PLINTH, 275, [44, 22, 18], 41, "furniture", "glass", "modern", ["coffee", "table", "glass"]),
  product("prd_coffee_iron", "Ironwork Coffee Table", OAK, 380, [50, 24, 18], 55, "furniture", "metal", "industrial", ["coffee", "table", "cocktail"]),
  // Side tables
  product("prd_side_nook", "Nook Side Table", NORD, 119, [18, 18, 22], 12, "furniture", "wood", "scandinavian", ["side", "table", "end"]),
  product("prd_side_cobalt", "Cobalt Side Table", BRIGHT, 145, [16, 16, 24], 14, "furniture", "metal", "modern", ["side", "table", "end"]),
  // Desks
  product("prd_desk_ledger", "Ledger Writing Desk", PLINTH, 489, [48, 24, 30], 52, "furniture", "wood", "mid-century", ["desk", "writing", "office"]),
  product("prd_desk_forge", "Forge Standing Desk", OAK, 699, [60, 30, 48], 78, "furniture", "metal", "industrial", ["desk", "standing", "office"]),
  // Desk chairs
  product("prd_dchair_ergo", "Ergo Mesh Desk Chair", BRIGHT, 249, [25, 25, 38], 32, "furniture", "fabric", "modern", ["desk", "chair", "office", "task"]),
  product("prd_dchair_clerk", "Clerk Swivel Desk Chair", OAK, 189, [24, 24, 36], 26, "furniture", "mixed", "vintage", ["desk", "chair", "swivel"]),
  product("prd_dchair_haven", "Haven Task Chair", NORD, 229, [26, 26, 40], 30, "furniture", "fabric", "scandinavian", ["desk", "chair", "task", "office"]),
  product("prd_dchair_exec", "Executive Desk Chair", PLINTH, 340, [28, 28, 42], 38, "furniture", "mixed", "modern", ["desk", "chair", "executive", "office"]),
  // Dining chairs
  product("prd_dining_spindle", "Spindle Dining Chair", NORD, 99, [18, 20, 34], 11, "furniture", "wood", "scandinavian", ["dining", "chair"]),
  product("prd_dining_bent", "Bentwood Dining Chair", PLINTH, 125, [19, 21, 32], 12, "furniture", "wood", "mid-century", ["dining", "chair", "bentwood"]),
  // TV stand
  product("prd_tv_lowline", "Lowline TV Stand", OAK, 349, [60, 16, 20], 64, "furniture", "wood", "modern", ["tv", "stand", "media", "console"]),
  // Floor lamps
  product("prd_flamp_arc", "Arc Floor Lamp", BRIGHT, 189, [14, 14, 66], 14, "lighting", "metal", "modern", ["floor", "lamp", "arc"]),
  product("prd_flamp_tripod", "Tripod Floor Lamp", NORD, 139, [15, 15, 62], 10, "lighting", "wood", "scandinavian", ["floor", "lamp", "tripod"]),
  product("prd_flamp_beacon", "Beacon Floor Lamp", OAK, 159, [12, 12, 60], 13, "lighting", "metal", "industrial", ["floor", "lamp", "standing"]),
  // Table lamps
  product("prd_tlamp_opal", "Opal Table Lamp", PLINTH, 79, [12, 12, 22], 5, "lighting", "glass", "modern", ["table", "lamp", "desk", "bedside"]),
  product("prd_tlamp_mushroom", "Mushroom Table Lamp", BRIGHT, 95, [13, 13, 18], 6, "lighting", "ceramic", "mid-century", ["table", "lamp", "bedside"]),
  product("prd_tlamp_banker", "Banker's Table Lamp", OAK, 89, [10, 8, 16], 4, "lighting", "metal", "vintage", ["table", "lamp", "desk"]),
  // Pendant
  product("prd_pendant_cone", "Cone Pendant Light", NORD, 129, [14, 14, 12], 6, "lighting", "metal", "industrial", ["pendant", "light", "ceiling"]),
  // Rugs
  product("prd_rug_dune", "Dune Area Rug 8x10", NORD, 399, [96, 120], 35, "rug", "fabric", "scandinavian", ["rug", "area", "8x10"]),
  product("prd_rug_loop", "Loop Area Rug 8x10", BRIGHT, 349, [96, 120], 32, "rug", "fabric", "modern", ["rug", "area", "8x10"]),
  product("prd_rug_atlas", "Atlas Area Rug 6x9", OAK, 249, [72, 108], 24, "rug", "mixed", "vintage", ["rug", "area", "6x9"]),
  product("prd_rug_kilim", "Kilim Area Rug 9x12", PLINTH, 589, [108, 144], 48, "rug", "fabric", "vintage", ["rug", "area", "9x12"]),
  product("prd_rug_runner", "Stripe Runner Rug 2x6", PLINTH, 79, [24, 72], 8, "rug", "fabric", "scandinavian", ["rug", "runner", "2x6"]),
  // Wall art
  product("prd_art_tidal", "Tidal Framed Poster", BRIGHT, 49, [24, 1, 36], 3, "wall-art", "mixed", "modern", ["poster", "print", "framed", "gallery"]),
  product("prd_art_grid", "Gridwork Gallery Print", NORD, 59, [24, 1, 30], 3, "wall-art", "wood", "scandinavian", ["poster", "print", "framed", "gallery"]),
  product("prd_art_monolith", "Monolith Poster", OAK, 39, [18, 1, 24], 2, "wall-art", "mixed", "brutalist", ["poster", "print", "unframed"]),
  product("prd_art_botanical", "Botanical Framed Print", PLINTH, 69, [24, 1, 36], 4, "wall-art", "wood", "vintage", ["poster", "print", "framed", "botanical"]),
  // Storage
  product("prd_store_shelf", "Stackline Bookshelf", OAK, 259, [32, 12, 72], 70, "storage", "wood", "industrial", ["bookshelf", "shelf", "bookcase"]),
  product("prd_store_basket", "Woven Storage Basket", NORD, 35, [14, 14, 12], 2, "storage", "fabric", "scandinavian", ["basket", "storage", "woven"]),
  // Decor
  product("prd_bookend_iron", "Cast Iron Bookends", OAK, 29, [5, 4, 7], 3, "decor", "metal", "industrial", ["bookend", "bookends", "iron"]),
  product("prd_bookend_slab", "Slab Bookends", BRIGHT, 34, [6, 5, 6], 5, "decor", "mixed", "brutalist", ["bookend", "bookends", "concrete"]),
  product("prd_bookend_arch", "Brass Arch Bookends", PLINTH, 42, [5, 4, 7], 3, "decor", "metal", "mid-century", ["bookend", "bookends", "brass"]),
  product("prd_pot_terracotta", "Terracotta Plant Pot", NORD, 18, [6, 6, 6], 2, "decor", "ceramic", "vintage", ["plant", "pot", "planter"]),
  product("prd_pot_glazed", "Glazed Plant Pot", BRIGHT, 22, [6, 6, 7], 2, "decor", "ceramic", "modern", ["plant", "pot", "planter"]),
  product("prd_pot_stoneware", "Stoneware Plant Pot", PLINTH, 24, [5, 5, 6], 2, "decor", "ceramic", "scandinavian", ["plant", "pot", "planter"]),
  product("prd_vase_ribbed", "Ribbed Vase", OAK, 38, [6, 6, 12], 3, "decor", "ceramic", "modern", ["vase", "flowers"]),
  product("prd_vase_bud", "Glass Bud Vase", NORD, 26, [4, 4, 8], 1, "decor", "glass", "scandinavian", ["vase", "bud", "flowers"]),
];

/** Products the user already owns, used for "imported from Shopify" objects. */
export const OWNED_PRODUCTS: Product[] = [
  product("prd_owned_lounge", "Cedar Lounge Chair", NORD, 460, [30, 31, 32], 26, "furniture", "wood", "scandinavian", ["armchair", "chair", "lounge"]),
  product("prd_owned_reading", "Halo Reading Lamp", BRIGHT, 120, [12, 12, 58], 8, "lighting", "metal", "modern", ["floor", "lamp", "reading"]),
  product("prd_owned_cube", "Cube Storage Shelf", OAK, 180, [30, 14, 30], 28, "storage", "wood", "industrial", ["shelf", "cube", "storage"]),
  product("prd_owned_sundial", "Sundial Framed Print", PLINTH, 55, [20, 1, 28], 3, "wall-art", "wood", "mid-century", ["print", "framed", "poster"]),
];

export function getProductById(id: string): Product | undefined {
  return PRODUCTS.find((p) => p.id === id) ?? OWNED_PRODUCTS.find((p) => p.id === id);
}

export function getStoreById(id: string): Store | undefined {
  return STORES.find((s) => s.id === id);
}

const STOP_WORDS = new Set(["a", "an", "and", "the", "of", "for", "with", "to", "in"]);

function normalizeWord(word: string): string {
  const w = word.toLowerCase();
  return w.length > 3 && w.endsWith("s") ? w.slice(0, -1) : w;
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 1 && !STOP_WORDS.has(w))
    .map(normalizeWord);
}

function fits(objectDim: number | undefined, productDim: number | undefined): boolean {
  if (objectDim === undefined || productDim === undefined) return true;
  return productDim <= objectDim * 1.15;
}

/**
 * Synchronous scoring shared by `mockSearch` and the seed data.
 * Returns up to 8 product ids ranked by score desc, then price asc.
 */
export function searchCatalogSync(object: SpaceObject): string[] {
  const tokens = new Set(tokenize([object.name, ...object.similarItems].join(" ")));
  const scored = PRODUCTS.filter((p) => {
    if (p.type !== object.type) return false;
    if (object.priceLimit !== undefined && p.price > object.priceLimit) return false;
    return fits(object.dims.x, p.dims.x) && fits(object.dims.y, p.dims.y) && fits(object.dims.z, p.dims.z);
  }).map((p) => {
    const words = new Set([...p.tags.flatMap(tokenize), ...tokenize(p.title)]);
    let score = 0;
    for (const t of tokens) if (words.has(t)) score += 3;
    const tokenHit = score > 0;
    if (object.material && p.material === object.material) score += 1;
    if (object.style && p.style === object.style) score += 1;
    return { p, score, tokenHit };
  });
  const anyTokenHit = scored.some((s) => s.tokenHit);
  return scored
    .filter((s) => s.score > 0 || !anyTokenHit)
    .sort((a, b) => b.score - a.score || a.p.price - b.p.price)
    .slice(0, 8)
    .map((s) => s.p.id);
}

const STEPS: SearchStep[] = ["planning", "building-query", "querying-catalog", "ranking"];

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

export function mockSearch(
  object: SpaceObject,
  onStep: (step: SearchStep) => void,
  signal?: AbortSignal,
): Promise<string[]> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onAbort = () => {
      if (timer !== undefined) clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    const run = (index: number) => {
      if (index >= STEPS.length) {
        signal?.removeEventListener("abort", onAbort);
        resolve(searchCatalogSync(object));
        return;
      }
      onStep(STEPS[index]);
      timer = setTimeout(() => run(index + 1), 450 + Math.random() * 100);
    };
    run(0);
  });
}
