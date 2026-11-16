type Task<T = unknown> = () => Promise<T>;

interface Debounced {
  timer: ReturnType<typeof setTimeout>;
  task: Task;
  onError: (error: unknown) => void;
}

const noop = () => {};

/**
 * Runs backend writes one at a time, in the order they were made, so a
 * placement never reaches the backend before its object. Rapid writes to the
 * same thing (keyboard nudges) can be debounced per key; any other write
 * first flushes them, which keeps the overall order intact.
 */
export class WriteQueue {
  private tail: Promise<void> = Promise.resolve();
  /** Tasks queued or running. */
  private queued = 0;
  private readonly debounced = new Map<string, Debounced>();

  /** Runs `task` after every earlier write (debounced ones included). */
  run<T>(task: Task<T>): Promise<T> {
    this.flush();
    return this.push(task);
  }

  /** Only the latest task per key runs, `delayMs` after the last call. */
  debounce(key: string, task: Task, delayMs: number, onError: (error: unknown) => void): void {
    const existing = this.debounced.get(key);
    if (existing) clearTimeout(existing.timer);
    const timer = setTimeout(() => {
      this.debounced.delete(key);
      this.push(task).catch(onError);
    }, delayMs);
    this.debounced.set(key, { timer, task, onError });
  }

  /** Queues every debounced write now. */
  flush(): void {
    const entries = [...this.debounced.values()];
    this.debounced.clear();
    for (const entry of entries) {
      clearTimeout(entry.timer);
      this.push(entry.task).catch(entry.onError);
    }
  }

  /** True while writes other than the one currently running are waiting. Call from inside a task. */
  hasOthersPending(): boolean {
    return this.queued > 1 || this.debounced.size > 0;
  }

  /** True while anything is waiting or running. */
  isBusy(): boolean {
    return this.queued > 0 || this.debounced.size > 0;
  }

  /** Drops debounced writes without sending them (on teardown). */
  dispose(): void {
    for (const entry of this.debounced.values()) clearTimeout(entry.timer);
    this.debounced.clear();
  }

  private push<T>(task: Task<T>): Promise<T> {
    this.queued += 1;
    const result = this.tail.then(task);
    this.tail = result.then(noop, noop).then(() => {
      this.queued -= 1;
    });
    return result;
  }
}
