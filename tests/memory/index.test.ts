import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { ExtensionContext, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseWithSchema } from "../../src/config/parse.ts";
import type { AppContext, GitApi } from "../../src/extensions/api.ts";
import memory, {
  type MemoryConfig,
  MemoryConfigSchema,
} from "../../src/extensions/memory/index.ts";
import { commitAll } from "../../src/git/commit.ts";
import { type AgentRunner, createCommitAgent } from "../../src/git/commit-agent.ts";
import { runGit } from "../../src/git/git.ts";
import { commitFile, initRepo } from "../git/helpers.ts";

const config = (overrides: unknown = {}): MemoryConfig =>
  parseWithSchema(MemoryConfigSchema, overrides, "memory config");

interface CronCall {
  name: string;
  schedule: string;
  run: () => Promise<void>;
}

interface SetupResult {
  cron: ReturnType<typeof vi.fn>;
  cronCalls: CronCall[];
  useFactory: ExtensionFactory;
  log: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn> };
  run: ReturnType<typeof vi.fn>;
}

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const setupWith = (
  extensionConfig: MemoryConfig,
  workspaceDir: string,
  side: Record<string, unknown>,
  commitAllImpl?: (options: Parameters<GitApi["commitAll"]>[0]) => Promise<string[]>,
): SetupResult => {
  const cronCalls: CronCall[] = [];
  const cron = vi.fn((name: string, schedule: string, run: () => Promise<void>) => {
    cronCalls.push({ name, schedule, run });
  });

  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn() };
  let useFactory: ExtensionFactory = (() => undefined) as unknown as ExtensionFactory;
  const use = vi.fn((factory: ExtensionFactory) => {
    useFactory = factory;
  });

  const defaultCommitAll = ({ log: callLog, ...options }: Parameters<GitApi["commitAll"]>[0]) =>
    commitAll({ ...options, log: callLog ?? (log as never) });

  const git = {
    commitAll: commitAllImpl ?? defaultCommitAll,
    createCommitAgent: (mode: "workspace" | "project") =>
      createCommitAgent(side as unknown as AgentRunner, mode),
    smartPush: vi.fn(),
    smartPull: vi.fn(),
  } as unknown as GitApi;

  memory.setup({
    extensionConfig,
    log,
    workspace: { root: workspaceDir, dataDir: join(workspaceDir, ".tachikoma") },
    bootstrap: vi.fn(),
    agent: { use, side },
    sessions: { registerProcessor: vi.fn() },
    scheduler: { cron },
    git,
  } as unknown as AppContext<MemoryConfig>);

  return { cron, cronCalls, useFactory, log, run: side.run as ReturnType<typeof vi.fn> };
};

const setup = async (extensionConfig: MemoryConfig): Promise<ReturnType<typeof vi.fn>> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
  tempDirs.push(workspaceDir);

  return setupWith(extensionConfig, workspaceDir, { run: vi.fn() }).cron;
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

  it("does nothing when the extension is disabled", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
    tempDirs.push(workspaceDir);

    const result = setupWith(config({ enabled: false }), workspaceDir, { run: vi.fn() });

    expect(result.cron).not.toHaveBeenCalled();
    expect(result.log.info).toHaveBeenCalledWith("memory extension disabled by configuration");
  });

  it("injects the memory context section via the bound factory", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-ext-"));
    tempDirs.push(workspaceDir);
    await mkdir(join(workspaceDir, "memories"));

    const { useFactory } = setupWith(config(), workspaceDir, { run: vi.fn() });

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

describe("memory maintenance commitChanges", () => {
  const runMaintenanceCron = async (
    name: string,
    workspaceDir: string,
    commitAllImpl?: (options: Parameters<GitApi["commitAll"]>[0]) => Promise<string[]>,
  ): Promise<SetupResult> => {
    const result = setupWith(
      config(),
      workspaceDir,
      { run: vi.fn().mockResolvedValue(undefined) },
      commitAllImpl,
    );
    const call = result.cronCalls.find((entry) => entry.name === name) as CronCall;

    await call.run();

    return result;
  };

  it("commits the changes the maintenance tick left behind", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-commit-"));
    tempDirs.push(workspaceDir);
    await initRepo(workspaceDir);
    await commitFile(workspaceDir, "seed.txt", "seed\n", "Seed");
    await runGit(workspaceDir, ["mv", "seed.txt", "renamed.txt"]);

    const result = await runMaintenanceCron("memory-context-maintenance", workspaceDir);

    expect(result.run).toHaveBeenCalledTimes(2);
    expect(result.log.info).toHaveBeenCalledWith(
      { message: "chore(memory): scheduled context file maintenance" },
      "committed memory maintenance changes",
    );
    expect(result.log.info).toHaveBeenCalledWith(
      { message: "chore(memory): scheduled context file maintenance" },
      "committed memory maintenance changes",
    );
    expect(await runGit(workspaceDir, ["log", "-1", "--format=%s"])).toBe(
      "chore(memory): scheduled context file maintenance",
    );
  });

  it("skips the committed log when the tree is already clean", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-clean-"));
    tempDirs.push(workspaceDir);
    await initRepo(workspaceDir);
    await commitFile(workspaceDir, "seed.txt", "seed\n", "Seed");

    const result = await runMaintenanceCron("memory-context-maintenance", workspaceDir);

    expect(result.log.info).not.toHaveBeenCalledWith(
      { message: "chore(memory): scheduled context file maintenance" },
      "committed memory maintenance changes",
    );
  });

  it("warns instead of throwing when the commit fails", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-memory-nogit-"));
    tempDirs.push(workspaceDir);

    const result = await runMaintenanceCron(
      "memory-episodic-maintenance",
      workspaceDir,
      async () => {
        throw new Error("disk full");
      },
    );

    expect(result.log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ err: expect.anything() }),
      "memory maintenance commit failed",
    );
  });
});
