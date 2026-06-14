import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { parseWithSchema } from "../../src/config/parse.ts";
import type { AppContext } from "../../src/extensions/api.ts";
import memory, {
  type MemoryConfig,
  MemoryConfigSchema,
} from "../../src/extensions/memory/index.ts";

const config = (overrides: unknown = {}): MemoryConfig =>
  parseWithSchema(MemoryConfigSchema, overrides, "memory config");

const setup = async (extensionConfig: MemoryConfig): Promise<ReturnType<typeof vi.fn>> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
  const cron = vi.fn();

  memory.setup({
    extensionConfig,
    log: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() },
    workspace: { root: workspaceDir, dataDir: join(workspaceDir, ".tachikoma") },
    bootstrap: vi.fn(),
    agent: { use: vi.fn(), side: {} },
    sessions: { registerProcessor: vi.fn() },
    scheduler: { cron },
  } as unknown as AppContext<MemoryConfig>);

  return cron;
};

describe("memory extension setup", () => {
  it("applies default transcript retention config", () => {
    expect(config().maintenance.transcriptRetentionDays).toBe(90);
    expect(config().maintenance.transcriptsSchedule).toBe("50 3 * * *");
  });

  it("registers the transcript prune cron when maintenance is enabled", async () => {
    const cron = await setup(config());

    expect(cron).toHaveBeenCalledTimes(5);
    expect(cron).toHaveBeenCalledWith(
      "memory-context-maintenance",
      "0 4 * * *",
      expect.any(Function),
    );
    expect(cron).toHaveBeenCalledWith(
      "memory-transcripts-maintenance",
      "50 3 * * *",
      expect.any(Function),
    );
  });

  it("registers no maintenance crons when maintenance is disabled", async () => {
    const cron = await setup(config({ maintenance: { enabled: false } }));

    expect(cron).not.toHaveBeenCalled();
  });
});
