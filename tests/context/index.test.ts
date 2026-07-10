import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppContext } from "../../src/extensions/api.ts";
import context from "../../src/extensions/context/index.ts";

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

type AgentUseFactory = (pi: {
  on: (event: string, handler: (event?: unknown, ctx?: unknown) => unknown) => void;
}) => void;

const setup = async (timezone?: string) => {
  const workspaceRoot = await mkdtemp(join(tmpdir(), "tachi-context-ext-"));
  tempDirs.push(workspaceRoot);

  const registerProcessor = vi.fn();
  const use = vi.fn();

  await context.setup({
    log: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() },
    workspace: {
      root: workspaceRoot,
      dataDir: join(workspaceRoot, ".tachikoma"),
      resolve: (...parts: string[]) => join(workspaceRoot, ...parts),
    },
    config: { scheduler: timezone ? { timezone } : {} },
    bootstrap: vi.fn(),
    agent: { use, systemPrompt: vi.fn(), forkAndContinue: vi.fn() },
    sessions: { registerProcessor },
  } as unknown as AppContext);

  return { registerProcessor, use };
};

describe("context extension setup", () => {
  it("registers its own core-context post-processor", async () => {
    const { registerProcessor } = await setup();

    expect(registerProcessor).toHaveBeenCalledTimes(1);

    const processor = registerProcessor.mock.calls[0]?.[0];
    expect(processor.name).toBe("core-context");
    expect(processor.phase).toBe("preFinalize");
  });

  it("injects the current date/time as a debounced hidden message in the configured timezone", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-10T12:00:00Z"));
    try {
      const { use } = await setup("America/New_York");

      // SOUL, USER, then the current-time provider.
      expect(use).toHaveBeenCalledTimes(3);
      const factory = use.mock.calls[2]?.[0] as AgentUseFactory;

      let handler: ((event?: unknown, ctx?: unknown) => unknown) | undefined;
      factory({
        on: (_event, h) => {
          handler = h;
        },
      });

      const result = (await handler?.({ systemPrompt: "BASE" }, {})) as
        | { message: { customType: string; content: string; display: boolean } }
        | undefined;

      expect(result).toEqual({
        message: {
          customType: "current-time",
          // 2026-07-10T12:00:00Z in America/New_York (EDT, UTC-4) is 08:00; the zone abbreviation
          // is left open since ICU may render it differently across builds.
          content: expect.stringMatching(/^Current date\/time: 2026-07-10 08:00 \S+$/),
          display: false,
        },
      });
    } finally {
      vi.useRealTimers();
    }
  });
});
