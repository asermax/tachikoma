import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import { registerReload } from "../../src/extensions/skills/reload.ts";
import type { Logger } from "../../src/log.ts";

const createFakeLog = () => {
  const log = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

interface Captured {
  commands: Map<string, { handler: (args: string, ctx: unknown) => Promise<unknown> }>;
  tools: Map<string, { execute: (...args: never[]) => Promise<{ content: unknown[] }> }>;
  sendUserMessage: ReturnType<typeof vi.fn>;
}

const fakePi = (): { pi: ExtensionAPI; captured: Captured } => {
  const captured: Captured = {
    commands: new Map(),
    tools: new Map(),
    sendUserMessage: vi.fn(),
  };

  const pi = {
    registerCommand: (name: string, options: unknown) =>
      captured.commands.set(name, options as never),
    registerTool: (definition: { name: string }) =>
      captured.tools.set(definition.name, definition as never),
    sendUserMessage: captured.sendUserMessage,
  } as unknown as ExtensionAPI;

  return { pi, captured };
};

describe("skills reload", () => {
  it("registers a /reload command that runs ctx.reload", async () => {
    const { pi, captured } = fakePi();
    registerReload(pi, createFakeLog());

    const reload = vi.fn();
    await captured.commands.get("reload")?.handler("", { reload });

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("registers a tool that queues /reload as a follow-up", async () => {
    const { pi, captured } = fakePi();
    registerReload(pi, createFakeLog());

    const result = await captured.tools.get("reload_resources")?.execute();

    expect(captured.sendUserMessage).toHaveBeenCalledWith("/reload", { deliverAs: "followUp" });
    expect(result?.content[0]).toMatchObject({ type: "text" });
  });
});
