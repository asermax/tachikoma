import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { persistentContextSection } from "../../src/agent/system-prompt-section.ts";

type Handler = (event?: unknown, ctx?: unknown) => unknown;

/** Capture the handlers a factory registers so the test can drive them directly. */
const drive = (factory: ReturnType<typeof persistentContextSection>) => {
  const handlers = new Map<string, Handler>();
  factory({ on: (event: string, handler: Handler) => handlers.set(event, handler) } as never);

  return {
    beforeAgentStart: (ctx?: unknown) =>
      handlers.get("before_agent_start")?.({ type: "before_agent_start" }, ctx) as Promise<
        { message: { customType: string; content: string; display: boolean } } | undefined
      >,
  };
};

describe("persistentContextSection", () => {
  it("injects a persisted hidden message once on before_agent_start", async () => {
    const driver = drive(
      persistentContextSection("tasks-usage", { provide: () => "use the task tools" }),
    );

    const first = await driver.beforeAgentStart();
    expect(first).toEqual({
      message: {
        customType: "tasks-usage",
        content: '<context owner="tasks-usage">\nuse the task tools\n</context>',
        display: false,
      },
    });

    // Subsequent agent runs in the same session do not re-inject.
    expect(await driver.beforeAgentStart()).toBeUndefined();
  });

  it("passes the session ctx to provide and awaits async providers", async () => {
    let seenCwd: string | undefined;
    const driver = drive(
      persistentContextSection("memories", {
        provide: async (ctx) => {
          seenCwd = ctx.cwd;
          return "  memory layout  ";
        },
      }),
    );

    const result = await driver.beforeAgentStart({ cwd: "/ws" } as ExtensionContext);

    expect(seenCwd).toBe("/ws");
    // Content is trimmed before wrapping.
    expect(result?.message.content).toBe('<context owner="memories">\nmemory layout\n</context>');
  });

  it("injects nothing when the content is empty, staying eligible for a later turn", async () => {
    let value = "   ";
    const driver = drive(persistentContextSection("memories", { provide: () => value }));

    expect(await driver.beforeAgentStart()).toBeUndefined();

    // Empty content does not consume the one-shot: once it has content, it injects.
    value = "memory layout";
    expect((await driver.beforeAgentStart())?.message.content).toBe(
      '<context owner="memories">\nmemory layout\n</context>',
    );
  });

  it("accepts a static string for provide", async () => {
    const driver = drive(persistentContextSection("git-usage", { provide: "use git tools" }));

    expect((await driver.beforeAgentStart())?.message.content).toBe(
      '<context owner="git-usage">\nuse git tools\n</context>',
    );
  });
});
