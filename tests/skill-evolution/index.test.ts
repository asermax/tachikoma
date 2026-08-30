import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { parseWithSchema } from "../../src/config/parse.ts";
import type { AppContext, PostProcessor } from "../../src/extensions/api.ts";
import skillEvolution, {
  type SkillEvolutionConfig,
  SkillEvolutionConfigSchema,
} from "../../src/extensions/skill-evolution/index.ts";
import { skillEvolutionDir } from "../../src/extensions/skill-evolution/layout.ts";
import { IMPACT_LOG_FILENAME } from "../../src/extensions/skill-evolution/store.ts";

// [extensions.skill-evolution] only reaches extensionConfig once the extension joins
// firstPartyExtensions at land time — these tests drive setup with an explicit config object.
const config = (overrides: unknown = {}): SkillEvolutionConfig =>
  parseWithSchema(SkillEvolutionConfigSchema, overrides, "skill-evolution config");

interface BootstrapCall {
  name: string;
  hook: () => void | Promise<void>;
}

interface SetupResult {
  bootstrapCalls: BootstrapCall[];
  processors: PostProcessor[];
  log: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn> };
}

const tempDirs: string[] = [];

afterEach(async () => {
  while (tempDirs.length > 0) {
    await rm(tempDirs.pop() as string, { recursive: true, force: true });
  }
});

const setupWith = (extensionConfig: SkillEvolutionConfig, workspaceDir: string): SetupResult => {
  const bootstrapCalls: BootstrapCall[] = [];
  const bootstrap = vi.fn((name: string, hook: () => void | Promise<void>) => {
    bootstrapCalls.push({ name, hook });
  });
  const processors: PostProcessor[] = [];
  const log = { info: vi.fn(), warn: vi.fn(), debug: vi.fn() };

  skillEvolution.setup({
    extensionConfig,
    log,
    workspace: {
      root: workspaceDir,
      dataDir: join(workspaceDir, ".tachikoma"),
      resolve: (...parts: string[]) => join(workspaceDir, ...parts),
    },
    bootstrap,
    sessions: {
      registerProcessor: vi.fn((processor: PostProcessor) => {
        processors.push(processor);
      }),
    },
    agent: { forkAndContinue: vi.fn(), branchFile: vi.fn(), side: { run: vi.fn() } },
    events: { emit: vi.fn() },
  } as unknown as AppContext<SkillEvolutionConfig>);

  return { bootstrapCalls, processors, log };
};

const setup = async (extensionConfig: SkillEvolutionConfig): Promise<SetupResult> => {
  const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-skill-evo-ext-"));
  tempDirs.push(workspaceDir);

  return setupWith(extensionConfig, workspaceDir);
};

describe("skill-evolution extension setup", () => {
  it("defaults to enabled with an optional post-work prompt", () => {
    expect(config().enabled).toBe(true);
    expect(config().postWorkPrompt).toBeUndefined();
    expect(config({ postWorkPrompt: "Open PRs" }).postWorkPrompt).toBe("Open PRs");
  });

  it("registers the layout bootstrap and the main-phase trunk-close processor when enabled", async () => {
    const { bootstrapCalls, processors } = await setup(config());

    expect(bootstrapCalls.map((call) => call.name)).toEqual(["init-skill-evolution-layout"]);

    // main phase = alongside memory's trunk close under the phased runner's allSettled.
    expect(processors).toHaveLength(1);
    expect(processors[0]).toMatchObject({
      name: "skill-evolution-trunk-close",
      phase: "main",
      statusLabel: "Evolving skills",
    });
  });

  it("seeds the store from the bootstrap hook (workspace root captured in closure)", async () => {
    const workspaceDir = await mkdtemp(join(tmpdir(), "tachi-skill-evo-ext-"));
    tempDirs.push(workspaceDir);

    const { bootstrapCalls } = setupWith(config(), workspaceDir);
    await bootstrapCalls[0]?.hook();

    const dir = skillEvolutionDir(workspaceDir);
    expect(await readFile(join(dir, "MEMORY.md"), "utf8")).toContain("# Skill Evolution Index");
    expect(await readFile(join(dir, IMPACT_LOG_FILENAME), "utf8")).toContain(
      "| Date | Skill | Pattern | Branch | Tip | Description | Status |",
    );
  });

  it("registers nothing — no bootstrap, no processor — when the extension is disabled (R13)", async () => {
    const { bootstrapCalls, processors, log } = await setup(config({ enabled: false }));

    expect(bootstrapCalls).toEqual([]);
    expect(processors).toEqual([]);
    expect(log.info).toHaveBeenCalledWith("skill-evolution extension disabled by configuration");
  });
});
