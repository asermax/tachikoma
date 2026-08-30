import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FILE_EDIT_TOOLS } from "../../src/agent/file-tools.ts";
import {
  type AnalysisDeps,
  analyzeBranches,
  runMaintenance,
} from "../../src/extensions/skill-evolution/analyze.ts";
import { skillEvolutionDir } from "../../src/extensions/skill-evolution/layout.ts";
import { maintenanceSystemPrompt } from "../../src/extensions/skill-evolution/prompts.ts";
import type { Logger } from "../../src/log.ts";
import {
  BRANCH_SUMMARY,
  getBranchRecords,
  isBranchAnalyzed,
  markBranchAnalyzed,
} from "../../src/sessions/trunk.ts";
import { fileExists } from "../../src/util/markdown-store.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-skill-analyze-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
  vi.clearAllMocks();
});

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

/**
 * A fake trunk session: an append-only entries list with the SessionManager methods the shared walk
 * + trunk markers touch. Same shape as memory's close-pipeline fake — no LLM, no disk.
 */
const makeSession = (initial: SessionEntry[]) => {
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

/** A trunk with three collapsed branches; each leaf/base resolves so all three records survive. */
const threeBranchTrunk = () =>
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

/** A trunk with a single collapsed branch — for tests that need exactly one analysis pass. */
const singleBranchTrunk = () =>
  makeSession([
    leafEntry("leaf-1"),
    branchSummaryEntry("s-1", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-1",
      originalLeafId: "leaf-1",
      baseId: null,
    }),
  ]);

const sessionFile = () => join(workspace, "trunk.jsonl");

const analysisDeps = (): { deps: AnalysisDeps; forkAndContinue: ReturnType<typeof vi.fn> } => {
  const forkAndContinue = vi.fn().mockResolvedValue(undefined);
  const branchFile = vi.fn((_sourceFile: string, leafId: string): string =>
    join(workspace, `branch-${leafId}.jsonl`),
  );

  return {
    deps: { agent: { forkAndContinue, branchFile }, workspaceRoot: workspace, log: fakeLog },
    forkAndContinue,
  };
};

describe("analyzeBranches", () => {
  it("analyzes each unmarked branch: one conversation-aware fork with the stamped instruction + FILE_EDIT_TOOLS, then a summaryEntryId-keyed marker", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = analysisDeps();

    const result = await analyzeBranches(session, sessionFile(), records, "2026-08-30", deps);

    expect(result.failed).toEqual([]);
    expect(forkAndContinue).toHaveBeenCalledTimes(3); // one fork per branch — no per-store fan-out

    for (const [i, record] of records.entries()) {
      const call = forkAndContinue.mock.calls[i];
      expect(call?.[0]).toBe(join(workspace, `branch-${record.originalLeafId}.jsonl`));
      // The instruction is stamped with the workspace, the trunk day, and this branch's id.
      expect(call?.[1]).toContain(workspace);
      expect(call?.[1]).toContain("2026-08-30");
      expect(call?.[1]).toContain(record.branchId);
      expect(call?.[2]).toBe("processor");
      expect(call?.[3]).toEqual(FILE_EDIT_TOOLS);
    }

    for (const record of records) {
      expect(isBranchAnalyzed(session, record.summaryEntryId)).toBe(true);
    }
  });

  it("writes the marker only after the body settles (marker-after-body, DES-008)", async () => {
    const session = singleBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = analysisDeps();

    forkAndContinue.mockImplementation(async () => {
      // Mid-body, the branch must not carry its marker yet.
      expect(isBranchAnalyzed(session, "s-1")).toBe(false);
    });

    await analyzeBranches(session, sessionFile(), records, "2026-08-30", deps);

    expect(isBranchAnalyzed(session, "s-1")).toBe(true);
  });

  it("sweeps emptied pages from the store after each fork (the fork has no delete tool)", async () => {
    const session = singleBranchTrunk();
    const records = getBranchRecords(session);
    const { deps } = analysisDeps();

    const dir = skillEvolutionDir(workspace);
    await mkdir(dir, { recursive: true });
    const emptied = join(dir, "merged-away.md");
    await writeFile(emptied, "");

    await analyzeBranches(session, sessionFile(), records, "2026-08-30", deps);

    await expect(fileExists(emptied)).resolves.toBe(false);
  });

  it("skips a branch already carrying an analysis marker (idempotent re-close, R12)", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = analysisDeps();

    markBranchAnalyzed(session, records[1]?.summaryEntryId ?? ""); // topic-2 already analyzed

    const result = await analyzeBranches(session, sessionFile(), records, "2026-08-30", deps);

    expect(result.failed).toEqual([]);
    expect(forkAndContinue).toHaveBeenCalledTimes(2);
    expect(forkAndContinue.mock.calls[0]?.[1]).toContain("topic-1");
    expect(forkAndContinue.mock.calls[1]?.[1]).toContain("topic-3");
  });

  it("isolates a failing branch into the failed list (marker absent) and never throws (R11)", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = analysisDeps();

    forkAndContinue.mockImplementation(async (sourceFile: string) => {
      if (sourceFile.includes("leaf-2")) throw new Error("analysis boom");
    });

    const result = await analyzeBranches(session, sessionFile(), records, "2026-08-30", deps);

    expect(result.failed.map((record) => record.branchId)).toEqual(["topic-2"]);
    expect(isBranchAnalyzed(session, records[0]?.summaryEntryId ?? "")).toBe(true);
    expect(isBranchAnalyzed(session, records[1]?.summaryEntryId ?? "")).toBe(false);
    expect(isBranchAnalyzed(session, records[2]?.summaryEntryId ?? "")).toBe(true);
  });

  it("emits Analyzing skills — branch i/n progress lines over the day's full set", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = analysisDeps();
    const status = vi.fn();

    forkAndContinue.mockImplementation(async (sourceFile: string) => {
      if (sourceFile.includes("leaf-2")) throw new Error("boom");
    });

    await analyzeBranches(session, sessionFile(), records, "2026-08-30", { ...deps, status });

    // Positions count every branch in the day's set (skipped or failed ones included).
    expect(status.mock.calls.map((call) => call[0])).toEqual([
      "Analyzing skills — branch 1/3…",
      "Analyzed skills — branch 1/3",
      "Analyzing skills — branch 2/3…",
      "Skill analysis failed — branch 2/3",
      "Analyzing skills — branch 3/3…",
      "Analyzed skills — branch 3/3",
    ]);
  });
});

describe("runMaintenance", () => {
  it("skips the LLM pass entirely when the store has no pattern pages", async () => {
    const run = vi.fn().mockResolvedValue({ text: "done" });
    await mkdir(skillEvolutionDir(workspace), { recursive: true });

    await runMaintenance({ side: { run }, workspaceRoot: workspace, log: fakeLog });

    expect(run).not.toHaveBeenCalled();
  });

  it("runs the headless maintenance agent with the composed system prompt, then sweeps the store", async () => {
    const run = vi.fn().mockResolvedValue({ text: "done" });

    const dir = skillEvolutionDir(workspace);
    await mkdir(dir, { recursive: true });
    const blanked = join(dir, "deduped-away.md");
    await writeFile(blanked, "");

    await runMaintenance({ side: { run }, workspaceRoot: workspace, log: fakeLog });

    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith({
      tools: FILE_EDIT_TOOLS,
      system: maintenanceSystemPrompt(workspace),
      prompt: "Perform the maintenance pass now, following your instructions.",
      tier: "processor",
    });

    // The host sweep follows the run: whatever the agent emptied is removed.
    await expect(fileExists(blanked)).resolves.toBe(false);
  });
});
