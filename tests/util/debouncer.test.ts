import { describe, expect, it, vi } from "vitest";

import { createDebouncedTask } from "../../src/util/debouncer.ts";
import { fakeLogger } from "../git/helpers.ts";

const wait = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

describe("createDebouncedTask", () => {
  it("fires once after the delay", async () => {
    const task = vi.fn(async () => {});
    const debouncer = createDebouncedTask(task, 30, fakeLogger());

    debouncer.touch();
    await wait(15);
    expect(task).not.toHaveBeenCalled();

    await wait(30);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it("resets the timer on each touch — fires once after the last", async () => {
    const task = vi.fn(async () => {});
    const debouncer = createDebouncedTask(task, 30, fakeLogger());

    debouncer.touch();
    await wait(15);
    debouncer.touch();
    await wait(15);
    debouncer.touch();
    await wait(15);
    expect(task).not.toHaveBeenCalled();

    await wait(30);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it("clear() cancels a pending fire", async () => {
    const task = vi.fn(async () => {});
    const debouncer = createDebouncedTask(task, 30, fakeLogger());

    debouncer.touch();
    debouncer.clear();
    await wait(60);
    expect(task).not.toHaveBeenCalled();
  });

  it("is a no-op when disabled (delay <= 0)", async () => {
    const task = vi.fn(async () => {});
    const debouncer = createDebouncedTask(task, 0, fakeLogger());

    debouncer.touch();
    await wait(60);
    expect(task).not.toHaveBeenCalled();
  });

  it("coalesces a fire due during a run into a single re-run", async () => {
    let resolve = (): void => {};
    const task = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r;
        }),
    );
    const debouncer = createDebouncedTask(task, 30, fakeLogger());

    debouncer.touch();
    await wait(45); // fire → run 1 starts and blocks
    expect(task).toHaveBeenCalledTimes(1);

    debouncer.touch();
    await wait(45); // fire becomes due while run 1 is active → deferred
    expect(task).toHaveBeenCalledTimes(1);

    resolve(); // finish run 1 → the coalesced re-run starts
    await wait(10);
    expect(task).toHaveBeenCalledTimes(2);

    resolve(); // finish run 2
    await debouncer.whenIdle();
    expect(task).toHaveBeenCalledTimes(2);
  });

  it("warns and swallows a task rejection", async () => {
    const log = fakeLogger();
    const debouncer = createDebouncedTask(
      async () => {
        throw new Error("boom");
      },
      30,
      log,
    );

    debouncer.touch();
    await wait(45); // let the timer fire and the rejected task be caught
    await debouncer.whenIdle();

    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ err: expect.any(Error) }),
      "debounced task failed",
    );
  });

  it("whenIdle resolves immediately when nothing is running", async () => {
    const debouncer = createDebouncedTask(async () => {}, 30, fakeLogger());
    await expect(debouncer.whenIdle()).resolves.toBeUndefined();
  });
});
