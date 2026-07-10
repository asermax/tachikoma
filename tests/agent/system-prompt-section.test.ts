import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_CONTEXT_REFRESH_MS,
  dueForInjection,
  provideContext,
  provideDebouncedContext,
} from "../../src/agent/system-prompt-section.ts";

type Handler = (event?: unknown, ctx?: unknown) => unknown;

type Result =
  | { systemPrompt: string }
  | { message: { customType: string; content: string; display: boolean } }
  | undefined;

/** Capture the handlers a factory registers so the test can drive them directly. */
const drive = (factory: ReturnType<typeof provideContext>) => {
  const handlers = new Map<string, Handler>();
  factory({ on: (event: string, handler: Handler) => handlers.set(event, handler) } as never);

  return {
    beforeAgentStart: (systemPrompt = "BASE", ctx?: unknown) =>
      handlers.get("before_agent_start")?.(
        { type: "before_agent_start", systemPrompt },
        ctx,
      ) as Promise<Result>,
  };
};

describe("provideContext", () => {
  describe("system-prompt mode (no customType)", () => {
    it("appends content to the turn's system prompt, no XML wrapper", async () => {
      const driver = drive(provideContext("use the workspace"));

      expect(await driver.beforeAgentStart("BASE")).toEqual({
        systemPrompt: "BASE\n\nuse the workspace",
      });
    });

    it("computes content once and re-appends it every turn", async () => {
      let calls = 0;
      const driver = drive(
        provideContext(() => {
          calls += 1;
          return "soul";
        }),
      );

      expect(await driver.beforeAgentStart("BASE1")).toEqual({ systemPrompt: "BASE1\n\nsoul" });
      // A later turn rebuilds the override, so the append must re-fire — using the cached content.
      expect(await driver.beforeAgentStart("BASE2")).toEqual({ systemPrompt: "BASE2\n\nsoul" });
      expect(calls).toBe(1);
    });

    it("passes the session ctx to provide and awaits async providers, trimming the result", async () => {
      let seenCwd: string | undefined;
      const driver = drive(
        provideContext(async (ctx) => {
          seenCwd = ctx.cwd;
          return "  memory layout  ";
        }),
      );

      const result = await driver.beforeAgentStart("BASE", { cwd: "/ws" } as ExtensionContext);

      expect(seenCwd).toBe("/ws");
      expect(result).toEqual({ systemPrompt: "BASE\n\nmemory layout" });
    });

    it("contributes nothing while empty, staying eligible for a later turn", async () => {
      let value = "   ";
      const driver = drive(provideContext(() => value));

      expect(await driver.beforeAgentStart()).toBeUndefined();

      value = "soul";
      expect(await driver.beforeAgentStart("BASE")).toEqual({ systemPrompt: "BASE\n\nsoul" });
    });
  });

  describe("message mode (customType)", () => {
    it("injects a persisted hidden message once, no XML wrapper", async () => {
      const driver = drive(provideContext("use the task tools", "tasks-usage"));

      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "tasks-usage", content: "use the task tools", display: false },
      });

      // Subsequent agent runs in the same session do not re-inject.
      expect(await driver.beforeAgentStart()).toBeUndefined();
    });

    it("injects nothing when the content is empty, staying eligible for a later turn", async () => {
      let value = "   ";
      const driver = drive(provideContext(() => value, "memories"));

      expect(await driver.beforeAgentStart()).toBeUndefined();

      value = "memory layout";
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "memories", content: "memory layout", display: false },
      });
    });
  });

  describe("debounced message mode (customType + debounceMs)", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("injects on the first turn, then suppresses within the window", async () => {
      vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
      const driver = drive(provideDebouncedContext(() => "v1", "my-ctx", 30 * 60 * 1000));

      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "v1", display: false },
      });

      // T+29 min — still within the 30-min window, suppressed.
      vi.setSystemTime(new Date("2026-07-10T12:29:00Z"));
      expect(await driver.beforeAgentStart()).toBeUndefined();
    });

    it("re-injects at the window boundary with content re-resolved from provide", async () => {
      vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
      let value = "first";
      const driver = drive(provideDebouncedContext(() => value, "my-ctx", 30 * 60 * 1000));

      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "first", display: false },
      });

      // Change the live value; within the window it is NOT re-read.
      value = "second";
      vi.setSystemTime(new Date("2026-07-10T12:29:00Z"));
      expect(await driver.beforeAgentStart()).toBeUndefined();

      // At the boundary the provider re-resolves, carrying the new value.
      vi.setSystemTime(new Date("2026-07-10T12:30:00Z"));
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "second", display: false },
      });
    });

    it("does not consume the window when content is empty", async () => {
      vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
      let value = "   ";
      const driver = drive(provideDebouncedContext(() => value, "my-ctx", 30 * 60 * 1000));

      // Empty → contributes nothing and stays eligible.
      expect(await driver.beforeAgentStart()).toBeUndefined();

      // One minute later (well within what would be the window) — non-empty injects now.
      value = "real";
      vi.setSystemTime(new Date("2026-07-10T12:01:00Z"));
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "real", display: false },
      });
    });

    it("works with a static-string provider", async () => {
      vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
      const driver = drive(provideDebouncedContext("static guidance", "my-ctx", 30 * 60 * 1000));

      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "static guidance", display: false },
      });

      vi.setSystemTime(new Date("2026-07-10T12:10:00Z"));
      expect(await driver.beforeAgentStart()).toBeUndefined();

      // After the window the same static content is re-injected.
      vi.setSystemTime(new Date("2026-07-10T12:40:00Z"));
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "static guidance", display: false },
      });
    });

    it("defaults the window to 30 minutes (DEFAULT_CONTEXT_REFRESH_MS)", async () => {
      vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
      const driver = drive(provideDebouncedContext(() => "v", "my-ctx"));

      expect(DEFAULT_CONTEXT_REFRESH_MS).toBe(30 * 60 * 1000);
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "v", display: false },
      });

      // T+29 min still suppressed with the default window.
      vi.setSystemTime(new Date("2026-07-10T12:29:00Z"));
      expect(await driver.beforeAgentStart()).toBeUndefined();

      // T+30 min (the default boundary) re-injects.
      vi.setSystemTime(new Date("2026-07-10T12:30:00Z"));
      expect(await driver.beforeAgentStart()).toEqual({
        message: { customType: "my-ctx", content: "v", display: false },
      });
    });
  });
});

describe("dueForInjection (pure gate)", () => {
  const window = 30 * 60 * 1000;
  const t = 1_000_000;

  it("is due when never injected (null)", () => {
    expect(dueForInjection(null, t, window)).toBe(true);
  });

  it("is not due within the window", () => {
    expect(dueForInjection(t, t + 29 * 60 * 1000, window)).toBe(false);
  });

  it("is due at exactly the boundary (>=)", () => {
    expect(dueForInjection(t, t + 30 * 60 * 1000, window)).toBe(true);
  });

  it("is due after the boundary", () => {
    expect(dueForInjection(t, t + 45 * 60 * 1000, window)).toBe(true);
  });
});
