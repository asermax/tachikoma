import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { ExtensionContext, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseWithSchema } from "../../src/config/parse.ts";
import type { AppContext, GitApi, PostProcessor } from "../../src/extensions/api.ts";
import memory, {
  type MemoryConfig,
  MemoryConfigSchema,
} from "../../src/extensions/memory/index.ts";

const config = (overrides: unknown = {}): MemoryConfig =>
  parseWithSchema(MemoryConfigSchema, overrides, "memory config");

interface CronCall {
  name: string;
  schedule: string;
  run: () => Promise<void>;
}

interface SetupResult {
  cronCalls: CronCall[];
  cron: ReturnType<typeof vi.fn>;
  processors: PostProcessor[];
  useFactory: ExtensionFactory;
  log: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn> };
}

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const setupWith = (extensionConfig: MemoryConfig, workspaceDir: string): SetupResult => {
  const cronCalls: CronCall[] = [];
  const cron = vi.fn((name: string, schedule: string, run: () => Promise<void>) => {
    cronCalls.push({ name, schedule, run });
  });

  const processors: PostProcessor[] = [];
  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn() };
  let useFactory: ExtensionFactory = (() => undefined) as unknown as ExtensionFactory;
  const use = vi.fn((factory: ExtensionFactory) => {
    useFactory = factory;
  });

  const git = {
    commitAll: vi.fn().mockResolvedValue([]),
    createCommitAgent: vi.fn(),
    smartPush: vi.fn(),
    smartPull: vi.fn(),
  } as unknown as GitApi;

  memory.setup({
    extensionConfig,
    log,
    workspace: { root: workspaceDir, dataDir: join(workspaceDir, ".tachikoma") },
    bootstrap: vi.fn(),
    agent: { use, side: { run: vi.fn() } },
    sessions: { registerProcessor: (p: PostProcessor) => processors.push(p) },
    scheduler: { cron },
    git,
  } as unknown as AppContext<MemoryConfig>);

  return { cronCalls, cron, processors, useFactory, log };
};

const setup = async (extensionConfig: MemoryConfig): Promise<SetupResult> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
  tempDirs.push(workspaceDir);

  return setupWith(extensionConfig, workspaceDir);
};

describe("memory extension setup", () => {
  it("applies default transcript retention config", () => {
    expect(config().maintenance.transcriptRetentionDays).toBe(90);
    expect(config().maintenance.transcriptsSchedule).toBe("50 3 * * *");
  });

  it("registers ONLY the deterministic transcript-prune cron — no agent maintenance crons", async () => {
    const { cron, cronCalls } = await setup(config());

    expect(cron).toHaveBeenCalledTimes(1);
    expect(cronCalls.map((call) => call.name)).toEqual(["memory-transcripts-prune"]);
    expect(cronCalls[0]?.schedule).toBe("50 3 * * *");

    // The five former nightly maintenance crons folded into the trunk-close pipeline.
    for (const name of [
      "memory-episodic-maintenance",
      "memory-facts-maintenance",
      "memory-preferences-maintenance",
      "memory-context-maintenance",
      "memory-transcripts-maintenance",
    ]) {
      expect(cronCalls.some((call) => call.name === name)).toBe(false);
    }
  });

  it("registers the trunk-close pipeline + transcript archive post-processors", async () => {
    const { processors } = await setup(config());

    expect(processors.map((p) => p.name)).toEqual(["memory-trunk-close", "transcript-archive"]);
    expect(processors[0]?.phase).toBe("main");
    expect(processors[1]?.phase).toBe("finalize");
  });

  it("registers no trunk-close pipeline when maintenance is disabled (archive + prune stay)", async () => {
    const { processors, cronCalls } = await setup(config({ maintenance: { enabled: false } }));

    expect(processors.map((p) => p.name)).toEqual(["transcript-archive"]);
    expect(cronCalls.map((call) => call.name)).toEqual(["memory-transcripts-prune"]);
  });

  it("does nothing when the extension is disabled", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
    tempDirs.push(workspaceDir);

    const result = setupWith(config({ enabled: false }), workspaceDir);

    expect(result.cron).not.toHaveBeenCalled();
    expect(result.processors).toEqual([]);
    expect(result.log.info).toHaveBeenCalledWith("memory extension disabled by configuration");
  });

  it("injects the memory context section via the bound factory", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
    tempDirs.push(workspaceDir);
    await mkdir(join(workspaceDir, "memories"));

    const { useFactory } = setupWith(config(), workspaceDir);

    const handlers: Record<string, (event: unknown, ctx: ExtensionContext) => Promise<unknown>> =
      {};
    const pi = {
      on: (event: string, handler: (event: unknown, ctx: ExtensionContext) => Promise<unknown>) => {
        handlers[event] = handler;
      },
    };

    useFactory(pi as unknown as Parameters<ExtensionFactory>[0]);

    await handlers.session_start?.({}, {} as ExtensionContext);
    const injection = (await handlers.before_agent_start?.({}, {} as ExtensionContext)) as {
      message: { content: string };
    };

    expect(injection.message.content).toContain("memories/");
  });
});
