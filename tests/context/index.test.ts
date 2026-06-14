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

const setup = async () => {
  const workspaceRoot = await mkdtemp(join(tmpdir(), "tachi-context-ext-"));
  tempDirs.push(workspaceRoot);

  const registerProcessor = vi.fn();

  await context.setup({
    log: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() },
    workspace: {
      root: workspaceRoot,
      dataDir: join(workspaceRoot, ".tachikoma"),
      resolve: (...parts: string[]) => join(workspaceRoot, ...parts),
    },
    bootstrap: vi.fn(),
    agent: { use: vi.fn(), systemPrompt: vi.fn(), forkAndContinue: vi.fn() },
    sessions: { registerProcessor },
  } as unknown as AppContext);

  return { registerProcessor };
};

describe("context extension setup", () => {
  it("registers its own core-context post-processor", async () => {
    const { registerProcessor } = await setup();

    expect(registerProcessor).toHaveBeenCalledTimes(1);

    const processor = registerProcessor.mock.calls[0]?.[0];
    expect(processor.name).toBe("core-context");
    expect(processor.phase).toBe("preFinalize");
  });
});
