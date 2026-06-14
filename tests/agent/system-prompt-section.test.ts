import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";

import { persistentContextSection } from "../../src/agent/system-prompt-section.ts";

type Handler = (event?: unknown, ctx?: unknown) => unknown;

/** Capture the handlers a factory registers so the test can drive them directly. */
const drive = (factory: ReturnType<typeof persistentContextSection>) => {
  const handlers = new Map<string, Handler>();
  factory({ on: (event: string, handler: Handler) => handlers.set(event, handler) } as never);

  return {
    sessionStart: (ctx?: unknown) =>
      handlers.get("session_start")?.({ type: "session_start", reason: "startup" }, ctx),
    beforeAgentStart: () =>
      handlers.get("before_agent_start")?.() as
        | { message: { customType: string; content: string; display: boolean } }
        | undefined,
  };
};

describe("persistentContextSection", () => {
  it("prepares on session_start and injects a persisted hidden message once", async () => {
    const driver = drive(
      persistentContextSection("tasks-usage", { provide: () => "use the task tools" }),
    );

    // No injection before session_start prepared the content.
    expect(driver.beforeAgentStart()).toBeUndefined();

    await driver.sessionStart();

    const first = driver.beforeAgentStart();
    expect(first).toEqual({
      message: {
        customType: "tasks-usage",
        content: '<context owner="tasks-usage">\nuse the task tools\n</context>',
        display: false,
      },
    });

    // Subsequent agent runs in the same session do not re-inject.
    expect(driver.beforeAgentStart()).toBeUndefined();
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

    await driver.sessionStart({ cwd: "/ws" } as ExtensionContext);

    expect(seenCwd).toBe("/ws");
    // Content is trimmed before wrapping.
    expect(driver.beforeAgentStart()?.message.content).toBe(
      '<context owner="memories">\nmemory layout\n</context>',
    );
  });

  it("injects nothing when the prepared content is empty", async () => {
    const driver = drive(persistentContextSection("memories", { provide: () => "   " }));

    await driver.sessionStart();

    expect(driver.beforeAgentStart()).toBeUndefined();
  });

  it("accepts a static string for provide", async () => {
    const driver = drive(persistentContextSection("git-usage", { provide: "use git tools" }));

    await driver.sessionStart();

    expect(driver.beforeAgentStart()?.message.content).toBe(
      '<context owner="git-usage">\nuse git tools\n</context>',
    );
  });
});
