import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AgentSession, SessionEntry, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HeadlessRunOptions } from "../../src/agent/side-run.ts";
import { DISPATCH_BACKGROUND_TASK_EVENT, NOTIFY_EVENT } from "../../src/events.ts";
import type { TrunkPostContext } from "../../src/extensions/api.ts";
import {
  createSkillEvolutionProcessor,
  type SkillEvolutionProcessorDeps,
  type SkillEvolutionStages,
} from "../../src/extensions/skill-evolution/index.ts";
import {
  ensureSkillEvolutionLayout,
  impactLogPath,
  skillEvolutionDir,
} from "../../src/extensions/skill-evolution/layout.ts";
import {
  ProposalRunError,
  proposalTmpDir,
  type ReportedProposal,
} from "../../src/extensions/skill-evolution/propose.ts";
import type { ReconcileCompleted } from "../../src/extensions/skill-evolution/reconcile.ts";
import {
  DEFAULT_POST_WORK_PROMPT,
  reportRun,
} from "../../src/extensions/skill-evolution/report.ts";
import {
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
  readImpactLog,
} from "../../src/extensions/skill-evolution/store.ts";
import { gitVerifyDeps } from "../../src/extensions/skill-evolution/verify.ts";
import { runGit } from "../../src/git/git.ts";
import { listRemoteBranchTips } from "../../src/git/remote.ts";
import type { Logger } from "../../src/log.ts";
import {
  BRANCH_SUMMARY,
  type BranchRecord,
  getBranchRecords,
  isBranchAnalyzed,
} from "../../src/sessions/trunk.ts";
import { fileExists } from "../../src/util/markdown-store.ts";
import { fakeLogger, makeTempDir, setupRemotePair } from "../git/helpers.ts";

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-skill-evo-proc-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
  vi.clearAllMocks();
});

// ---- fixtures ------------------------------------------------------------------

const completed = (over: Partial<ReconcileCompleted> = {}): ReconcileCompleted => ({
  aborted: false,
  defaultBranch: "main",
  classifications: [],
  updated: 0,
  ...over,
});

/** Every stage faked (DES-003) — each test overrides exactly the seams it drives. */
const fakeStages = (over: Partial<SkillEvolutionStages> = {}) => ({
  hasRemote: vi.fn(async () => true),
  reconcile: vi.fn(async () => completed()),
  analyze: vi.fn(async () => ({ failed: [] })),
  maintenance: vi.fn(async () => {}),
  proposal: vi.fn(async () => []),
  verify: vi.fn(async () => [] as ImpactLogEntry[]),
  sweep: vi.fn(async () => {}),
  report: vi.fn(),
  ...over,
});

const reportedProposal: ReportedProposal = {
  branch: "skill-evolution/deploy-env-flag",
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  description: "Add the --env flag to the deploy guidance",
};

const verifiedEntry: ImpactLogEntry = {
  date: "2027-03-04",
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  branch: "skill-evolution/deploy-env-flag",
  tip: "abc123def456",
  description: "Add the --env flag to the deploy guidance",
  status: IMPACT_LOG_STATUSES.proposed,
};

/** A stub trunk for fake-stage runs — the real session shape only matters to the real analyze. */
const stubTrunk = (): TrunkPostContext =>
  ({
    session: { sessionFile: join(workspace, "trunk.jsonl") } as unknown as AgentSession,
    sessionFile: join(workspace, "trunk.jsonl"),
    day: "2027-03-04",
    branchRecords: [
      {
        branchId: "topic-1",
        originalLeafId: "leaf-1",
        baseId: null,
        summaryEntryId: "s-1",
        lastExchange: null,
      },
    ],
  }) as TrunkPostContext;

interface RunResult {
  emit: ReturnType<typeof vi.fn>;
  log: Logger;
  status: ReturnType<typeof vi.fn>;
}

const runProcessor = async (
  deps: Partial<SkillEvolutionProcessorDeps>,
  trunk: TrunkPostContext | null,
): Promise<RunResult> => {
  const emit = vi.fn();
  const status = vi.fn();
  const log = fakeLogger();

  const processor = createSkillEvolutionProcessor({
    agent: { forkAndContinue: vi.fn(), branchFile: vi.fn() },
    // runMaintenance reads `result.text` — the default fake keeps the real maintenance stage alive.
    side: { run: vi.fn(async () => ({ text: "" })) },
    workspaceRoot: workspace,
    tmpDir: proposalTmpDir(workspace),
    emit,
    ...deps,
  } as SkillEvolutionProcessorDeps);

  // The fail-soft contract under test is precisely that this NEVER rejects.
  await processor.process({ trunk, transcriptPath: null, log, status });

  return { emit, log, status };
};

const notifyPayloads = (emit: ReturnType<typeof vi.fn>): { text: string }[] =>
  emit.mock.calls
    .filter(([event]) => event === NOTIFY_EVENT)
    .map(([, payload]) => payload as { text: string });

const dispatchPayloads = (emit: ReturnType<typeof vi.fn>): { prompt: string; goal: string }[] =>
  emit.mock.calls
    .filter(([event]) => event === DISPATCH_BACKGROUND_TASK_EVENT)
    .map(([, payload]) => payload as { prompt: string; goal: string });

/** Seed pattern pages so the eligibility gate opens — tests that drive proposal/verify call this. */
const seedPatternPages = async (...names: string[]): Promise<void> => {
  const dir = skillEvolutionDir(workspace);
  await mkdir(dir, { recursive: true });

  for (const name of names) {
    await writeFile(join(dir, name), `# ${name}\n\n## Problem\nfriction.\n`, "utf8");
  }
};

describe("skill-evolution-trunk-close (structural stage fakes)", () => {
  it("registers as a main-phase processor with the Evolving skills label", () => {
    const processor = createSkillEvolutionProcessor({
      agent: { forkAndContinue: vi.fn(), branchFile: vi.fn() },
      side: { run: vi.fn() },
      workspaceRoot: workspace,
      tmpDir: join(workspace, "tmp"),
      emit: vi.fn(),
    });

    expect(processor.name).toBe("skill-evolution-trunk-close");
    expect(processor.phase).toBe("main");
    expect(processor.statusLabel).toBe("Evolving skills");
  });

  it("a headless close (no trunk) is a no-op — no gate, no stages, no notifications", async () => {
    const stages = fakeStages();
    const { emit } = await runProcessor({ stages }, null);

    expect(stages.hasRemote).not.toHaveBeenCalled();
    expect(stages.reconcile).not.toHaveBeenCalled();
    expect(stages.analyze).not.toHaveBeenCalled();
    expect(emit).not.toHaveBeenCalled();
  });

  it("no origin remote → nothing runs, nothing is written, the skip is logged (R15)", async () => {
    const stages = fakeStages({ hasRemote: vi.fn(async () => false) });
    const { emit, log } = await runProcessor({ stages }, stubTrunk());

    expect(stages.reconcile).not.toHaveBeenCalled();
    expect(stages.analyze).not.toHaveBeenCalled();
    expect(stages.maintenance).not.toHaveBeenCalled();
    expect(stages.proposal).not.toHaveBeenCalled();
    expect(stages.verify).not.toHaveBeenCalled();

    // Nothing written: the run never seeded or touched the store.
    await expect(fileExists(impactLogPath(workspace))).resolves.toBe(false);

    expect(log.info).toHaveBeenCalledWith("skill-evolution run skipped: no origin");
    // The skip is silent to the user — distinct from a reconcile abort, which notifies.
    expect(emit).not.toHaveBeenCalled();
  });

  it("a reconcile soft-abort emits a warning notification and skips analysis entirely (R1)", async () => {
    const stages = fakeStages({
      reconcile: vi.fn(async () => ({
        aborted: true,
        reason: "reconciliation aborted: git fetch origin --prune failed (exit 128)",
      })),
    });
    const { emit, log } = await runProcessor({ stages }, stubTrunk());

    expect(stages.analyze).not.toHaveBeenCalled();
    expect(stages.maintenance).not.toHaveBeenCalled();
    expect(stages.proposal).not.toHaveBeenCalled();
    await expect(fileExists(impactLogPath(workspace))).resolves.toBe(false);

    expect(log.warn).toHaveBeenCalledWith(
      "reconciliation aborted: git fetch origin --prune failed (exit 128)",
    );
    expect(notifyPayloads(emit)).toEqual([
      {
        text: expect.stringContaining("fetch origin --prune failed"),
        severity: "warning",
        source: "skill-evolution",
      },
    ]);
  });

  it("a throwing analysis stage fails soft: warn + notify, never rethrown (R11)", async () => {
    const stages = fakeStages({
      analyze: vi.fn(async () => {
        throw new Error("walk blew up");
      }),
    });

    const { emit, log } = await runProcessor({ stages }, stubTrunk());

    expect(log.warn).toHaveBeenCalled();
    expect(notifyPayloads(emit).map((payload) => payload.severity)).toEqual(["warning"]);
  });

  it("a throwing maintenance stage fails soft the same way", async () => {
    const stages = fakeStages({
      maintenance: vi.fn(async () => {
        throw new Error("maintenance blew up");
      }),
    });

    const { emit } = await runProcessor({ stages }, stubTrunk());

    expect(notifyPayloads(emit).map((payload) => payload.text)).toEqual([
      "Skill evolution failed: maintenance blew up",
    ]);
  });

  it("no eligible patterns → the run ends after maintenance with one namespace sweep — no proposal LLM call, no dispatch", async () => {
    const stages = fakeStages();

    await runProcessor({ stages }, stubTrunk());

    expect(stages.maintenance).toHaveBeenCalledTimes(1);
    // The no-proposal path still heals a hard crash's orphans (S8): exactly one sweep, after
    // maintenance, and the proposing stages never fire.
    expect(stages.sweep).toHaveBeenCalledTimes(1);
    expect(stages.sweep).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: workspace }),
    );
    expect(stages.proposal).not.toHaveBeenCalled();
    expect(stages.verify).not.toHaveBeenCalled();
    expect(stages.report).not.toHaveBeenCalled();
  });

  it("eligibility is enforced input-side: only patterns with no ledger entry reach the proposal", async () => {
    await ensureSkillEvolutionLayout(workspace, fakeLogger());
    const dir = skillEvolutionDir(workspace);
    await writeFile(join(dir, "deploy-env-flag.md"), "# deploy-env-flag\n", "utf8");
    await writeFile(join(dir, "settled-pattern.md"), "# settled-pattern\n", "utf8");
    await writeFile(
      impactLogPath(workspace),
      [
        "# Skill Impact Log",
        "",
        "| Date | Skill | Pattern | Branch | Tip | Description | Status |",
        "| ---- | ----- | ------- | ------ | --- | ----------- | ------ |",
        "| 2027-03-01 | commit | [settled-pattern](./settled-pattern.md) | skill-evolution/settled | abc | done | accepted |",
        "",
      ].join("\n"),
      "utf8",
    );

    const stages = fakeStages({ proposal: vi.fn(async () => []) });

    await runProcessor({ stages }, stubTrunk());

    expect(stages.proposal).toHaveBeenCalledTimes(1);
    expect(stages.proposal.mock.calls[0]?.[0]).toMatchObject({ eligible: ["deploy-env-flag.md"] });
    // The sweep is exactly-once-per-run: on the proposing path it belongs to verification's
    // `finally` (faked away here), so the processor itself must not have swept too.
    expect(stages.sweep).not.toHaveBeenCalled();
  });

  it("at least one verified proposal dispatches exactly once; the status lines bracket the run", async () => {
    await seedPatternPages("deploy-env-flag.md");

    const stages = fakeStages({
      proposal: vi.fn(async () => [reportedProposal]),
      verify: vi.fn(async () => [verifiedEntry]),
      report: reportRun,
    });

    const { emit, status } = await runProcessor({ stages }, stubTrunk());

    expect(dispatchPayloads(emit)).toHaveLength(1);
    expect(dispatchPayloads(emit)[0]?.prompt).toContain(DEFAULT_POST_WORK_PROMPT);
    expect(dispatchPayloads(emit)[0]?.goal).toContain("skill-evolution/deploy-env-flag");

    expect(status.mock.calls.map((call) => call[0])).toEqual([
      "Reconciling skill proposals…",
      "Verifying 1 proposals…",
    ]);
    // A clean run notifies nothing.
    expect(notifyPayloads(emit)).toEqual([]);
  });

  it("zero verified proposals (a clean decline) dispatches nothing", async () => {
    const stages = fakeStages({
      proposal: vi.fn(async () => []),
      verify: vi.fn(async () => []),
      report: reportRun,
    });

    const { emit } = await runProcessor({ stages }, stubTrunk());

    expect(dispatchPayloads(emit)).toHaveLength(0);
    expect(notifyPayloads(emit)).toEqual([]);
  });

  it("a configured post-work prompt reaches the dispatch in place of the default", async () => {
    await seedPatternPages("deploy-env-flag.md");

    const stages = fakeStages({
      proposal: vi.fn(async () => [reportedProposal]),
      verify: vi.fn(async () => [verifiedEntry]),
      report: reportRun,
    });

    const { emit } = await runProcessor(
      { stages, postWorkPrompt: "Open pull requests for every verified proposal." },
      stubTrunk(),
    );

    expect(dispatchPayloads(emit)[0]?.prompt).toContain(
      "Open pull requests for every verified proposal.",
    );
    expect(dispatchPayloads(emit)[0]?.prompt).not.toContain(DEFAULT_POST_WORK_PROMPT);
  });

  it("a dying proposal run with a surviving capture still verifies, reports, and fails soft", async () => {
    await seedPatternPages("deploy-env-flag.md");

    const stages = fakeStages({
      proposal: vi.fn(async () => {
        throw new ProposalRunError("proposal agent run failed: model call died", {
          cause: new Error("model call died"),
          proposals: [reportedProposal],
        });
      }),
      verify: vi.fn(async () => [verifiedEntry]),
      report: reportRun,
    });

    const { emit } = await runProcessor({ stages }, stubTrunk());

    // The capture flowed into verification despite the throw…
    expect(stages.verify).toHaveBeenCalledTimes(1);
    expect(stages.verify.mock.calls[0]?.[0]).toMatchObject({ reported: [reportedProposal] });
    // …the dispatch still fired (≥1 verified, partial failure), and the failure warned.
    expect(dispatchPayloads(emit)).toHaveLength(1);
    expect(notifyPayloads(emit).map((payload) => payload.text)).toEqual([
      "Skill evolution failed: proposal agent run failed: model call died",
    ]);
  });

  it("a dying proposal run with NO capture still runs verification (over nothing) and fails soft", async () => {
    await seedPatternPages("deploy-env-flag.md");

    const stages = fakeStages({
      proposal: vi.fn(async () => {
        throw new Error("agent crashed before reporting");
      }),
      report: reportRun,
    });

    const { emit } = await runProcessor({ stages }, stubTrunk());

    expect(stages.verify).toHaveBeenCalledTimes(1);
    expect(stages.verify.mock.calls[0]?.[0]).toMatchObject({ reported: [] });
    expect(dispatchPayloads(emit)).toHaveLength(0);
    expect(notifyPayloads(emit)).toHaveLength(1);
  });
});

// ---- idempotency over the real walk (R12) ---------------------------------------

const branchSummaryEntry = (id: string, details: Record<string, unknown>): SessionEntry =>
  ({
    type: "branch_summary",
    id,
    fromId: (details.baseId as string | null) ?? "root",
    summary: "summary",
    details,
    fromHook: true,
  }) as unknown as SessionEntry;

const leafEntry = (id: string): SessionEntry =>
  ({ type: "message", id, message: { role: "assistant", content: [] } }) as unknown as SessionEntry;

/** A fake trunk session: append-only entries plus the SessionManager surface the walk touches. */
const makeSession = (initial: SessionEntry[]): AgentSession => {
  const entries = [...initial];
  let customCounter = 0;

  return {
    sessionFile: join(workspace, "trunk.jsonl"),
    sessionManager: {
      getEntries: () => [...entries],
      getEntry: (id: string) => entries.find((entry) => entry.id === id),
      appendCustomEntry: (customType: string, data: unknown): string => {
        customCounter += 1;
        const id = `c-${customCounter}`;
        entries.push({ type: "custom", id, customType, data } as unknown as SessionEntry);
        return id;
      },
    },
  } as unknown as AgentSession;
};

const threeBranchTrunk = (): AgentSession =>
  makeSession([
    leafEntry("leaf-1"),
    branchSummaryEntry("s-1", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-1",
      originalLeafId: "leaf-1",
      baseId: null,
    }),
    leafEntry("leaf-2"),
    branchSummaryEntry("s-2", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-2",
      originalLeafId: "leaf-2",
      baseId: "s-1",
    }),
    leafEntry("leaf-3"),
    branchSummaryEntry("s-3", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-3",
      originalLeafId: "leaf-3",
      baseId: "s-2",
    }),
  ]);

describe("skill-evolution-trunk-close idempotency (real walk, faked forker)", () => {
  it("a re-run close skips branches the first run marked (R12) — only the failed one re-forks", async () => {
    const session = threeBranchTrunk();
    const records: BranchRecord[] = getBranchRecords(session);

    const forkAndContinue = vi.fn(async (sourceFile: string, instruction: string) => {
      if (sourceFile.includes("leaf-2")) throw new Error("analysis boom");
      return { instruction };
    });
    const agent = {
      forkAndContinue,
      branchFile: vi.fn((_source: string, leafId: string): string =>
        join(workspace, `branch-${leafId}.jsonl`),
      ),
    };

    const trunk = {
      session,
      sessionFile: join(workspace, "trunk.jsonl"),
      day: "2027-03-04",
      branchRecords: records,
    } as TrunkPostContext;

    // First run: topic-2's fork fails; 1 and 3 complete and are marked. The git gates are faked
    // (the walk under test needs no repository — DES-003); the walk itself is the real one.
    const gates = { hasRemote: vi.fn(async () => true), reconcile: vi.fn(async () => completed()) };

    await runProcessor({ agent, stages: gates }, trunk);

    expect(forkAndContinue).toHaveBeenCalledTimes(3);
    expect(isBranchAnalyzed(session, records[0]?.summaryEntryId ?? "")).toBe(true);
    expect(isBranchAnalyzed(session, records[1]?.summaryEntryId ?? "")).toBe(false);
    expect(isBranchAnalyzed(session, records[2]?.summaryEntryId ?? "")).toBe(true);

    // The crash-recovery re-close (memory failed, trunk still open): only topic-2 re-forks.
    await runProcessor({ agent, stages: gates }, trunk);

    expect(forkAndContinue).toHaveBeenCalledTimes(4);
    expect(forkAndContinue.mock.calls.at(-1)?.[1]).toContain("topic-2");
  });
});

// ---- the partial-failure guarantee end to end (real git, bare origin) -----------

/** Invoke a tool's execute handler and unwrap its text content (the pi agent-loop shape). */
const invoke = async (tool: ToolDefinition, params: unknown): Promise<string> => {
  const result = await tool.execute(
    "test",
    params as never,
    undefined,
    undefined,
    undefined as never,
  );
  const text = result.content.find(
    (block): block is { type: "text"; text: string } => block.type === "text",
  );

  return text?.text ?? "";
};

describe("skill-evolution-trunk-close (real git, bare origin)", () => {
  let base: string;
  let ws: string;
  let tmpDir: string;

  beforeEach(async () => {
    base = await makeTempDir();
    ws = (await setupRemotePair(base)).cloneA;
    // Production shape: the worktrees live outside the repo, under the OS temp dir — nothing
    // proposal-side can dirty the main tree, so no gitignore scaffolding is needed.
    tmpDir = proposalTmpDir(ws);
    await mkdir(join(ws, "skills", "deploy"), { recursive: true });
    await writeFile(join(ws, "skills", "deploy", "SKILL.md"), "# Deploy\n\nGuidance.\n", "utf8");

    // A committed store with one eligible pattern page and an empty ledger.
    await ensureSkillEvolutionLayout(ws, fakeLogger());
    await writeFile(
      join(skillEvolutionDir(ws), "deploy-env-flag.md"),
      "# deploy-env-flag\n\n## Problem\n--env flag missing.\n",
      "utf8",
    );
    await runGit(ws, ["add", "-A"]);
    await runGit(ws, ["commit", "-m", "Seed skills and store"]);
    await runGit(ws, ["push", "origin", "main"]);
  });

  afterEach(async () => {
    await runGit(ws, ["worktree", "prune"]);
    await rm(tmpDir, { recursive: true, force: true });
    await rm(base, { recursive: true, force: true });
  });

  it("a proposal death after one successful push still verifies, logs, sweeps, dispatches — and fails soft", async () => {
    // The proposal agent (real runProposalAgent, faked SideRunner): authors one real proposal
    // through its own tool surface, reports it, then dies — the second proposal's model call.
    const run = vi.fn(async (options: HeadlessRunOptions): Promise<{ text: string }> => {
      const byName = new Map((options.customTools ?? []).map((tool) => [tool.name, tool] as const));
      const gitTool = byName.get("git");
      const writeFileTool = byName.get("write_file");
      const reportTool = byName.get("report_proposals");

      if (gitTool == null || writeFileTool == null || reportTool == null) {
        throw new Error("proposal tool surface incomplete");
      }

      const worktree = join(tmpDir, "deploy-env-flag");

      await invoke(gitTool, {
        args: [
          "worktree",
          "add",
          "-b",
          "skill-evolution/deploy-env-flag",
          worktree,
          "refs/remotes/origin/main",
        ],
      });
      await invoke(writeFileTool, {
        path: join(worktree, "skills", "deploy", "SKILL.md"),
        content: "# Deploy\n\nUse --env.\n",
      });
      await invoke(gitTool, { args: ["add", "-A"], path: worktree });
      await invoke(gitTool, { args: ["commit", "-m", "Propose the --env flag"], path: worktree });
      await invoke(gitTool, { args: ["push", "origin", "skill-evolution/deploy-env-flag"] });
      await invoke(reportTool, { proposals: [reportedProposal] });

      throw new Error("model call failed mid-run");
    });

    const emit = vi.fn();
    const log = fakeLogger();

    // Everything real except the maintenance pass (nothing to maintain) — hasRemote, reconcile,
    // the walk (over an empty trunk), the proposal run, verification, and the reporter all execute
    // against the real repository.
    const processor = createSkillEvolutionProcessor({
      agent: { forkAndContinue: vi.fn(), branchFile: vi.fn() },
      side: { run },
      workspaceRoot: ws,
      tmpDir,
      emit,
      now: () => new Date("2027-03-04T12:00:00Z"),
      stages: { maintenance: vi.fn(async () => {}) },
    });

    const session = makeSession([]);

    // Fail-soft contract: the processor resolves despite the proposal death.
    await processor.process({
      trunk: {
        session,
        sessionFile: session.sessionFile ?? join(ws, "trunk.jsonl"),
        day: "2027-03-04",
        branchRecords: getBranchRecords(session),
      },
      transcriptPath: null,
      log,
      status: vi.fn(),
    });

    // The pushed branch was verified and logged from git state with its remote tip.
    const remoteTip = (await listRemoteBranchTips(ws, "skill-evolution/*")).get(
      "skill-evolution/deploy-env-flag",
    );

    await expect(readImpactLog(impactLogPath(ws), fakeLogger())).resolves.toEqual([
      { ...verifiedEntry, tip: remoteTip },
    ]);

    // The sweep left nothing behind: no worktree under the tmp dir, no local proposal branch.
    const worktrees = (await gitVerifyDeps.listWorktrees(ws)).filter((path) =>
      path.startsWith(tmpDir),
    );
    expect(worktrees).toEqual([]);
    await expect(gitVerifyDeps.listLocalProposalBranches(ws)).resolves.toEqual([]);

    // The reporter fired exactly once (≥1 verified, partial failure), with the default prompt.
    const dispatches = emit.mock.calls.filter(
      ([event]) => event === DISPATCH_BACKGROUND_TASK_EVENT,
    );
    expect(dispatches).toHaveLength(1);

    const dispatchPayload = dispatches[0]?.[1];
    expect((dispatchPayload as { prompt: string }).prompt).toContain(DEFAULT_POST_WORK_PROMPT);

    // And the run still surfaced its failure: warn + one warning notification.
    expect(log.warn).toHaveBeenCalled();
    expect(
      emit.mock.calls
        .filter(([event]) => event === NOTIFY_EVENT)
        .map(([, payload]) => payload as { text: string }),
    ).toEqual([
      {
        text: "Skill evolution failed: proposal agent run failed: model call failed mid-run",
        severity: "warning",
        source: "skill-evolution",
      },
    ]);

    // The main working tree is untouched apart from the ledger row (the finalize commit's job).
    expect(await runGit(ws, ["status", "--porcelain"])).toBe(
      "M memories/skill-evolution/skill-impact-log.md",
    );
  });
});
