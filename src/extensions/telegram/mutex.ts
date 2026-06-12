/**
 * Promise-chain mutex serializing channel deliveries (TS analogue of the Python
 * DES-009 delivery lock): callers queue FIFO behind the in-flight task, so two
 * send sequences never interleave their Telegram API calls.
 */
export class Mutex {
  private tail: Promise<void> = Promise.resolve();

  run<T>(task: () => Promise<T>): Promise<T> {
    const result = this.tail.then(() => task());

    this.tail = result.then(
      () => undefined,
      () => undefined,
    );

    return result;
  }
}
