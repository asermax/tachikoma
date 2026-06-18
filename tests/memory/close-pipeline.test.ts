import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BranchSummaryDetails } from "../../src/agent/session-tree.ts";
import {
  CLOSE_STEPS,
  type CloseExtractionDeps,
  type ClosePhaseDeps,
  consolidatePhase,
  coreContextStep,
  createTrunkClosePipeline,
  extractBranches,
  prunePhase,
} from "../../src/extensions/memory/close-pipeline.ts";
import type { Logger } from "../../src/log.ts";
import {
  BRANCH_SUMMARY,
  getBranchRecords,
  isBranchExtracted,
  isStepDone,
} from "../../src/sessions/trunk.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

let workspace: string;

beforeEach(async () => {
  workspace = await mkdtemp(join(tmpdir(), "tachi-close-"));
});

afterEach(async () => {
  await rm(workspace, { recursive: true, force: true });
  vi.clearAllMocks();
});

const branchSummaryEntry = (id: string, details: BranchSummaryDetails): SessionEntry =>
  ({
    type: "branch_summary",
    id,
    fromId: details.baseId ?? "root",
    summary: "summary",
    details,
    fromHook: true,
  }) as unknown as SessionEntry;

const leafEntry = (id: string): SessionEntry =>
  ({ type: "message", id, message: { role: "assistant", content: [] } }) as unknown as SessionEntry;

/**
 * A fake trunk session: an append-only entries list with the SessionManager methods the close pipeline
 * + trunk markers touch (getEntries/getEntry/appendCustomEntry). Branch-file creation now lives on the
 * AgentManager (`branchFile`), not the live session, so the session no longer exposes
 * `createBranchedSession`. No LLM.
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

const extractionDeps = (): {
  deps: CloseExtractionDeps;
  forkAndContinue: ReturnType<typeof vi.fn>;
  branchFile: ReturnType<typeof vi.fn>;
  branchedLeaves: string[];
} => {
  const forkAndContinue = vi.fn().mockResolvedValue(undefined);
  const branchedLeaves: string[] = [];
  const branchFile = vi.fn((_sourceFile: string, leafId: string): string => {
    branchedLeaves.push(leafId);
    return join(workspace, `branch-${leafId}.jsonl`);
  });

  return {
    deps: { agent: { forkAndContinue, branchFile }, workspaceRoot: workspace },
    forkAndContinue,
    branchFile,
    branchedLeaves,
  };
};

const phaseDeps = (run = vi.fn().mockResolvedValue({ text: "done" })): ClosePhaseDeps => ({
  side: { run },
  workspaceRoot: workspace,
  settings: { recentDays: 15, weeklyThresholdMonths: 3, monthlyThresholdMonths: 12 },
  log: fakeLog,
});

describe("extractBranches", () => {
  it("extracts only unmarked branches (2 stores each) and writes a per-branch marker", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue, branchedLeaves } = extractionDeps();

    // Pre-mark topic-2 as already extracted (a prior run): it must be skipped.
    session.sessionManager.appendCustomEntry("tachikoma-completion-marker", {
      kind: "extracted-marker",
      branchId: "topic-2",
    });

    await extractBranches(session, records, deps, "2026-06-15", fakeLog);

    // Two unmarked branches × two stores (episodic + topics).
    expect(forkAndContinue).toHaveBeenCalledTimes(4);
    expect(branchedLeaves.sort()).toEqual(["leaf-1", "leaf-3"]);

    expect(isBranchExtracted(session, "topic-1")).toBe(true);
    expect(isBranchExtracted(session, "topic-2")).toBe(true);
    expect(isBranchExtracted(session, "topic-3")).toBe(true);
  });

  it("forks each branch's own conversation from its original leaf", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = extractionDeps();

    await extractBranches(session, records, deps, "2026-06-15", fakeLog);

    const sources = forkAndContinue.mock.calls.map((call) => call[0] as string);
    expect(sources.every((src) => src.startsWith(join(workspace, "branch-")))).toBe(true);
    // Per-branch focus is noted in the instruction handed to the fork, dated to the trunk's day (not
    // wall-clock) so a late close still files the episodic memory under the day it happened.
    expect(forkAndContinue.mock.calls[0]?.[1]).toContain("single topic branch");
    expect(forkAndContinue.mock.calls[0]?.[1]).toContain("2026-06-15");
  });

  it("re-run after a crash skips every branch that already carries a marker", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = extractionDeps();

    await extractBranches(session, records, deps, "2026-06-15", fakeLog);
    expect(forkAndContinue).toHaveBeenCalledTimes(6);

    forkAndContinue.mockClear();

    // A clean re-run (recovery / second close) extracts nothing — all markers present.
    await extractBranches(session, records, deps, "2026-06-15", fakeLog);
    expect(forkAndContinue).not.toHaveBeenCalled();
  });

  it("isolates a failing branch: extracts the rest, marks only those, and throws for retry", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = extractionDeps();

    // topic-2's first store extraction blows up; topic-1 and topic-3 must still complete.
    forkAndContinue.mockImplementation(async (sourceFile: string) => {
      if (sourceFile.includes("leaf-2")) throw new Error("extraction boom");
    });

    await expect(extractBranches(session, records, deps, "2026-06-15", fakeLog)).rejects.toThrow(
      "topic-2",
    );

    expect(isBranchExtracted(session, "topic-1")).toBe(true);
    expect(isBranchExtracted(session, "topic-2")).toBe(false);
    expect(isBranchExtracted(session, "topic-3")).toBe(true);

    // A retry re-runs only the unmarked branch (topic-2); it succeeds this time, no more throw.
    forkAndContinue.mockClear();
    forkAndContinue.mockResolvedValue(undefined);

    await extractBranches(session, records, deps, "2026-06-15", fakeLog);

    expect(forkAndContinue).toHaveBeenCalledTimes(2); // topic-2 × two stores
    expect(isBranchExtracted(session, "topic-2")).toBe(true);
  });

  it("returns without forking when the trunk session has no file", async () => {
    const session = threeBranchTrunk();
    (session as unknown as { sessionFile: string | null }).sessionFile = null;
    const records = getBranchRecords(session);
    const { deps, forkAndContinue } = extractionDeps();

    await extractBranches(session, records, deps, "2026-06-15", fakeLog);

    expect(forkAndContinue).not.toHaveBeenCalled();
  });
});

describe("ordered phases + step markers", () => {
  it("runs prune then consolidate then core-context, each marker-guarded", async () => {
    const session = threeBranchTrunk();
    const order: string[] = [];

    const run = vi.fn().mockImplementation(async ({ system }: { system: string }) => {
      order.push(system.includes("foundational context") ? "context" : "store");

      return { text: "ok" };
    });
    const deps = phaseDeps(run);

    await prunePhase(session, deps);
    await consolidatePhase(session, deps);
    await coreContextStep(session, deps);

    // prune = two store passes (episodic + topics); consolidate is an interim no-op (DLT-173 seam); core-context = one pass.
    expect(order).toEqual(["store", "store", "context"]);
    expect(isStepDone(session, CLOSE_STEPS.prune)).toBe(true);
    expect(isStepDone(session, CLOSE_STEPS.consolidate)).toBe(true);
    expect(isStepDone(session, CLOSE_STEPS.coreContext)).toBe(true);
  });

  it("a crash before a step marker repeats that phase; the marker gates the re-run", async () => {
    const session = threeBranchTrunk();

    // First attempt: the prune body throws AFTER mutating but BEFORE the marker is written.
    const failing = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue({ text: "done" });
    await expect(prunePhase(session, phaseDeps(failing))).rejects.toThrow("boom");

    // No marker was written — the staged result is not committed without it (marker/staging machinery).
    expect(isStepDone(session, CLOSE_STEPS.prune)).toBe(false);

    // Recovery re-runs the phase cleanly and now writes the marker.
    const run = vi.fn().mockResolvedValue({ text: "done" });
    await prunePhase(session, phaseDeps(run));
    expect(run).toHaveBeenCalledTimes(2);
    expect(isStepDone(session, CLOSE_STEPS.prune)).toBe(true);

    // A subsequent re-run skips the completed phase entirely.
    run.mockClear();
    await prunePhase(session, phaseDeps(run));
    expect(run).not.toHaveBeenCalled();
  });
});

describe("createTrunkClosePipeline", () => {
  it("runs extraction → prune → consolidate → core-context in order over the trunk", async () => {
    const session = threeBranchTrunk();
    const records = getBranchRecords(session);

    const events: string[] = [];
    const forkAndContinue = vi.fn().mockImplementation(async () => {
      events.push("extract");
    });
    const branchFile = vi.fn((_src: string, leafId: string) =>
      join(workspace, `branch-${leafId}.jsonl`),
    );
    const run = vi.fn().mockImplementation(async ({ system }: { system: string }) => {
      events.push(system.includes("foundational context") ? "context" : "store");

      return { text: "done" };
    });

    const processor = createTrunkClosePipeline({
      extraction: { agent: { forkAndContinue, branchFile }, workspaceRoot: workspace },
      phases: phaseDeps(run),
    });

    expect(processor.name).toBe("memory-trunk-close");
    expect(processor.phase).toBe("main");

    await processor.process({
      trunk: { session, sessionFile: "/s/trunk.jsonl", day: "2026-06-15", branchRecords: records },
      transcriptPath: "/s/trunk.jsonl",
      log: fakeLog,
    });

    // Six extracts (3 branches × 2 stores), then prune (2 store), consolidate (no-op), core (context).
    expect(events.slice(0, 6)).toEqual(Array(6).fill("extract"));
    expect(events.slice(6)).toEqual(["store", "store", "context"]);
  });

  it("no-ops for a background run with no trunk", async () => {
    const forkAndContinue = vi.fn();
    const branchFile = vi.fn();
    const run = vi.fn();

    const processor = createTrunkClosePipeline({
      extraction: { agent: { forkAndContinue, branchFile }, workspaceRoot: workspace },
      phases: phaseDeps(run),
    });

    await processor.process({ trunk: null, transcriptPath: null, log: fakeLog });

    expect(forkAndContinue).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
  });
});
