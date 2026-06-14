import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { provideContext } from "../../src/agent/system-prompt-section.ts";

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
});
