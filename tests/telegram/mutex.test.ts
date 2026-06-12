import { describe, expect, it, vi } from "vitest";

import { Mutex } from "../../src/extensions/telegram/mutex.ts";
import { deliverText } from "../../src/extensions/telegram/sending.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("Mutex", () => {
  it("runs tasks strictly in submission order", async () => {
    const mutex = new Mutex();
    const events: string[] = [];

    const slow = mutex.run(async () => {
      events.push("a-start");
      await sleep(20);
      events.push("a-end");
      return "a";
    });
    const fast = mutex.run(async () => {
      events.push("b-start");
      events.push("b-end");
      return "b";
    });

    expect(await Promise.all([slow, fast])).toEqual(["a", "b"]);
    expect(events).toEqual(["a-start", "a-end", "b-start", "b-end"]);
  });

  it("keeps the queue alive after a rejected task", async () => {
    const mutex = new Mutex();

    await expect(mutex.run(async () => Promise.reject(new Error("boom")))).rejects.toThrow("boom");
    expect(await mutex.run(async () => "recovered")).toBe("recovered");
  });
});

describe("delivery serialization", () => {
  it("never interleaves the API calls of two concurrent deliveries", async () => {
    const mutex = new Mutex();
    const calls: string[] = [];
    let next = 0;

    const api = {
      sendMessage: vi.fn().mockImplementation(async (_chat: number, text: string) => {
        calls.push(`send:${text}`);
        // Yield so an unserialized concurrent delivery would interleave here.
        await sleep(10);
        next += 1;
        return { message_id: next };
      }),
      sendChatAction: vi.fn(),
      copyMessage: vi.fn().mockImplementation(async (_chat: number, _from: number, id: number) => {
        calls.push(`copy:${id}`);
        return { message_id: 100 + id };
      }),
      deleteMessage: vi.fn().mockImplementation(async (_chat: number, id: number) => {
        calls.push(`delete:${id}`);
        return true;
      }),
    };

    await Promise.all([
      mutex.run(() => deliverText(api, 42, "first", true, fakeLog, 0)),
      mutex.run(() => deliverText(api, 42, "second", true, fakeLog, 0)),
    ]);

    expect(calls).toEqual([
      "send:first",
      "copy:1",
      "delete:1",
      "send:second",
      "copy:2",
      "delete:2",
    ]);
  });
});
