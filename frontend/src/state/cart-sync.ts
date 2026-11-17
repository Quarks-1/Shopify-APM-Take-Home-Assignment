// Per-store scheduler for merchant cart syncs (POST /api/cart/stores/<domain>/sync/).
// Syncs are slow (they call the merchant over UCP), so they run outside the
// serial write queue: each store waits SYNC_DEBOUNCE_MS after its last change,
// runs at most one request at a time, and runs exactly once more when a change
// arrives while its request is in flight. The backend always rebuilds the
// merchant cart from its own lines, so a sync never carries client data and
// duplicate or late syncs do no harm.
import type { ApiMerchantCart } from "@/lib/api";

export const SYNC_DEBOUNCE_MS = 500;

export interface CartSyncCallbacks {
  /** Sends one sync request. */
  run(storeId: string, force: boolean): Promise<ApiMerchantCart>;
  /** A request for the store started. */
  onStart(storeId: string): void;
  /** A request answered. `settled` is false when another request for the store is already waiting. */
  onResult(storeId: string, cart: ApiMerchantCart, settled: boolean): void;
  /** A request failed (the backend was unreachable or answered non-2xx). */
  onError(storeId: string, error: unknown, settled: boolean): void;
}

interface Entry {
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: boolean;
  /** A change arrived while the request was in flight. */
  rerun: boolean;
  /** The next request bypasses the backend's freshness and unsupported checks. */
  force: boolean;
}

export class CartSyncScheduler {
  private readonly entries = new Map<string, Entry>();
  private readonly callbacks: CartSyncCallbacks;

  constructor(callbacks: CartSyncCallbacks) {
    this.callbacks = callbacks;
  }

  /** Syncs the store `delayMs` after the last call for it. */
  schedule(storeId: string, { force = false, delayMs = SYNC_DEBOUNCE_MS }: { force?: boolean; delayMs?: number } = {}): void {
    const entry = this.entry(storeId);
    entry.force ||= force;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      if (entry.inFlight) entry.rerun = true;
      else this.start(storeId, entry);
    }, delayMs);
  }

  /** True while a sync for the store is waiting, running, or due to run again. */
  isBusy(storeId: string): boolean {
    const entry = this.entries.get(storeId);
    return entry !== undefined && (entry.timer !== null || entry.inFlight || entry.rerun);
  }

  private entry(storeId: string): Entry {
    let entry = this.entries.get(storeId);
    if (!entry) {
      entry = { timer: null, inFlight: false, rerun: false, force: false };
      this.entries.set(storeId, entry);
    }
    return entry;
  }

  private start(storeId: string, entry: Entry): void {
    const force = entry.force;
    entry.force = false;
    entry.inFlight = true;
    this.callbacks.onStart(storeId);
    let request: Promise<ApiMerchantCart>;
    try {
      request = this.callbacks.run(storeId, force);
    } catch (error) {
      request = Promise.reject(error);
    }
    request.then(
      (cart) => this.finish(storeId, entry, (settled) => this.callbacks.onResult(storeId, cart, settled)),
      (error: unknown) => this.finish(storeId, entry, (settled) => this.callbacks.onError(storeId, error, settled)),
    );
  }

  private finish(storeId: string, entry: Entry, report: (settled: boolean) => void): void {
    entry.inFlight = false;
    const rerun = entry.rerun;
    try {
      // isBusy() still counts the pending rerun while the callback runs.
      report(!rerun && entry.timer === null);
    } finally {
      entry.rerun = false;
      if (rerun) this.start(storeId, entry);
      else if (entry.timer === null && this.entries.get(storeId) === entry) this.entries.delete(storeId);
    }
  }
}
