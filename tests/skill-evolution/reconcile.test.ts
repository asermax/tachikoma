import { mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";
import {
  ensureSkillEvolutionLayout,
  impactLogPath,
  skillEvolutionDir,
} from "../../src/extensions/skill-evolution/layout.ts";
import {
  CLASSIFY_OUTCOMES,
  type ClassifyDeps,
  classify,
  gitReconcileDeps,
  type ReconcileAborted,
  type ReconcileCompleted,
  type ReconcileDeps,
  type ReconcileResult,
  reconcileProposals,
} from "../../src/extensions/skill-evolution/reconcile.ts";
import {
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
  readImpactLog,
  writeImpactLog,
} from "../../src/extensions/skill-evolution/store.ts";
import { type GitResult, runGit } from "../../src/git/git.ts";
import { commitFile, fakeLogger, makeTempDir, setupRemotePair } from "../git/helpers.ts";

const DEFAULT_REMOTE_REF = "refs/remotes/origin/main";

const log = fakeLogger();

const proposedRow = (branch: string, tip: string): ImpactLogEntry => ({
  date: "2026-08-30",
  skill: "commit",
  pattern: "commit-flag-missing.md",
  branch,
  tip,
  description: "Add the -s flag to the commit guidance",
  status: IMPACT_LOG_STATUSES.proposed,
});

// Narrow a completed/aborted result after asserting which it is, so the tests read the fields
// without casts.
const completed = (result: ReconcileResult): ReconcileCompleted => {
  expect(result.aborted).toBe(false);
  return result as ReconcileCompleted;
};

const aborted = (result: ReconcileResult): ReconcileAborted => {
  expect(result.aborted).toBe(true);
  return result as ReconcileAborted;
};

describe("classify (fake deps)", () => {
  const probes = (
    ancestorExit: number,
    trackingExists: boolean,
  ): { deps: ClassifyDeps; isAncestor: Mock; trackingRefExists: Mock } => {
    const isAncestor = vi.fn(async (): Promise<number> => ancestorExit);
    const trackingRefExists = vi.fn(async (): Promise<boolean> => trackingExists);
    return {
      deps: { defaultRemoteRef: DEFAULT_REMOTE_REF, isAncestor, trackingRefExists },
      isAncestor,
      trackingRefExists,
    };
  };

  it.each([
    {
      name: "exit 0 → accepted, whether or not the branch still exists",
      ancestorExit: 0,
      trackingExists: false,
      outcome: CLASSIFY_OUTCOMES.accepted,
      status: IMPACT_LOG_STATUSES.accepted,
      probesTracking: false,
    },
    {
      name: "exit 1 + tracking ref present → pending, status stays proposed",
      ancestorExit: 1,
      trackingExists: true,
      outcome: CLASSIFY_OUTCOMES.pending,
      status: IMPACT_LOG_STATUSES.proposed,
      probesTracking: true,
    },
    {
      name: "exit 1 + tracking ref absent → rejected",
      ancestorExit: 1,
      trackingExists: false,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
      probesTracking: true,
    },
    {
      name: "exit 128 (unresolvable object, post-squash GC) → rejected without probing the branch",
      ancestorExit: 128,
      trackingExists: true,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
      probesTracking: false,
    },
    {
      name: "any other exit → rejected",
      ancestorExit: 2,
      trackingExists: true,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
      probesTracking: false,
    },
  ])("$name", async ({ ancestorExit, trackingExists, outcome, status, probesTracking }) => {
    const { deps, isAncestor, trackingRefExists } = probes(ancestorExit, trackingExists);
    const entry = proposedRow("skill-evolution/commit-flag-missing", "abc123");

    const record = await classify(entry, deps);

    expect(record.branch).toBe(entry.branch);
    expect(record.tip).toBe(entry.tip);
    expect(record.outcome).toBe(outcome);
    expect(record.status).toBe(status);
    expect(record.reason).not.toBe("");
    expect(isAncestor).toHaveBeenCalledWith("abc123", DEFAULT_REMOTE_REF);
    if (probesTracking) {
      expect(trackingRefExists).toHaveBeenCalledWith(entry.branch);
    } else {
      expect(trackingRefExists).not.toHaveBeenCalled();
    }
  });
});

describe("reconcileProposals (fake deps)", () => {
  const okFetch: GitResult = { code: 0, stdout: "", stderr: "" };

  const fakeDeps = (over: Partial<ReconcileDeps> = {}): ReconcileDeps => ({
    fetchRemote: vi.fn(async (): Promise<GitResult> => okFetch),
    resolveRemoteDefaultBranch: vi.fn(async (): Promise<string> => "main"),
    isAncestor: vi.fn(async (): Promise<number> => 1),
    trackingRefExists: vi.fn(async (): Promise<boolean> => true),
    readImpactLog: vi.fn(async (): Promise<ImpactLogEntry[]> => []),
    writeImpactLog: vi.fn(async (): Promise<void> => {}),
    ...over,
  });

  it("a failed fetch soft-aborts before the ledger is even read", async () => {
    const deps = fakeDeps({
      fetchRemote: vi.fn(async () => ({ code: 128, stdout: "", stderr: "no route" })),
    });

    const result = await reconcileProposals({ workspaceRoot: "/workspace", log, deps });

    expect(aborted(result).reason).toMatch(/fetch/);
    expect(deps.readImpactLog).not.toHaveBeenCalled();
    expect(deps.writeImpactLog).not.toHaveBeenCalled();
  });

  it("a default-branch resolution failure soft-aborts before classification", async () => {
    const deps = fakeDeps({
      resolveRemoteDefaultBranch: vi.fn(async () => {
        throw new Error("could not resolve origin's default branch");
      }),
    });

    const result = await reconcileProposals({ workspaceRoot: "/workspace", log, deps });

    expect(aborted(result).reason).toMatch(/could not resolve/);
    expect(deps.readImpactLog).not.toHaveBeenCalled();
    expect(deps.writeImpactLog).not.toHaveBeenCalled();
  });

  it("classifies only proposed rows and leaves the ledger untouched while everything pends", async () => {
    const settled = {
      ...proposedRow("skill-evolution/old", "old-tip"),
      status: IMPACT_LOG_STATUSES.accepted,
    };
    const deps = fakeDeps({
      readImpactLog: vi.fn(async () => [settled, proposedRow("skill-evolution/open", "open-tip")]),
    });

    const result = completed(await reconcileProposals({ workspaceRoot: "/workspace", log, deps }));

    expect(result.classifications).toHaveLength(1);
    expect(result.classifications[0]).toMatchObject({
      branch: "skill-evolution/open",
      outcome: CLASSIFY_OUTCOMES.pending,
      status: IMPACT_LOG_STATUSES.proposed,
    });
    expect(result.updated).toBe(0);
    expect(deps.writeImpactLog).not.toHaveBeenCalled();
  });

  it("writes changed statuses back through the store, keeping non-proposed rows untouched", async () => {
    const settled = {
      ...proposedRow("skill-evolution/old", "old-tip"),
      status: IMPACT_LOG_STATUSES.rejected,
    };
    const open = proposedRow("skill-evolution/merged", "merged-tip");
    const writeImpactLog = vi.fn(async (): Promise<void> => {});
    const deps = fakeDeps({
      isAncestor: vi.fn(async (): Promise<number> => 0),
      readImpactLog: vi.fn(async () => [settled, open]),
      writeImpactLog,
    });

    const result = completed(await reconcileProposals({ workspaceRoot: "/workspace", log, deps }));

    expect(result.updated).toBe(1);
    expect(result.classifications[0]).toMatchObject({
      branch: "skill-evolution/merged",
      outcome: CLASSIFY_OUTCOMES.accepted,
    });

    const [, rows] = writeImpactLog.mock.calls[0] ?? [];
    expect(rows?.[0]).toBe(settled);
    expect(rows?.[1]).toMatchObject({
      branch: "skill-evolution/merged",
      status: IMPACT_LOG_STATUSES.accepted,
    });
  });
});

describe("reconcileProposals (integration: real git, bare origin)", () => {
  const BRANCH = "skill-evolution/commit-flag-missing";

  let base: string;

  beforeEach(async () => {
    base = await makeTempDir();
  });

  afterEach(async () => {
    await rm(base, { recursive: true, force: true });
  });

  const setupWorkspace = async () => {
    const { origin, cloneA: ws, cloneB: peer } = await setupRemotePair(base);
    return { origin, ws, peer };
  };

  // Author the proposal the way the previous run did: branch from main, push, record the tip.
  const pushProposal = async (ws: string, branch: string): Promise<string> => {
    await runGit(ws, ["checkout", "-b", branch]);
    await commitFile(ws, "proposal.txt", "propose\n", `Propose ${branch}`);
    await runGit(ws, ["push", "-u", "origin", branch]);
    const tip = await runGit(ws, ["rev-parse", branch]);
    await runGit(ws, ["checkout", "main"]);
    return tip;
  };

  const seedLedger = async (ws: string, rows: readonly ImpactLogEntry[]): Promise<void> => {
    await mkdir(skillEvolutionDir(ws), { recursive: true });
    await writeImpactLog(impactLogPath(ws), rows);
  };

  it("a merge-commit merge → accepted (branch deleted after merging)", async () => {
    const { ws, peer } = await setupWorkspace();
    const tip = await pushProposal(ws, BRANCH);
    await seedLedger(ws, [proposedRow(BRANCH, tip)]);

    await runGit(peer, ["fetch", "origin"]);
    await runGit(peer, ["merge", "--no-ff", `origin/${BRANCH}`, "-m", "Merge proposal"]);
    await runGit(peer, ["push", "origin", "main"]);
    await runGit(peer, ["push", "origin", "--delete", BRANCH]);

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.updated).toBe(1);
    expect(result.classifications[0]).toMatchObject({
      branch: BRANCH,
      outcome: CLASSIFY_OUTCOMES.accepted,
      status: IMPACT_LOG_STATUSES.accepted,
    });
    await expect(readImpactLog(impactLogPath(ws), log)).resolves.toEqual([
      { ...proposedRow(BRANCH, tip), status: IMPACT_LOG_STATUSES.accepted },
    ]);
  });

  it("a squash merge with the branch deleted → rejected", async () => {
    const { ws, peer } = await setupWorkspace();
    const tip = await pushProposal(ws, BRANCH);
    await seedLedger(ws, [proposedRow(BRANCH, tip)]);

    await runGit(peer, ["fetch", "origin"]);
    await runGit(peer, ["merge", "--squash", `origin/${BRANCH}`]);
    await runGit(peer, ["commit", "-m", "Squash proposal"]);
    await runGit(peer, ["push", "origin", "main"]);
    await runGit(peer, ["push", "origin", "--delete", BRANCH]);

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.updated).toBe(1);
    expect(result.classifications[0]).toMatchObject({
      branch: BRANCH,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
    });
    await expect(readImpactLog(impactLogPath(ws), log)).resolves.toEqual([
      { ...proposedRow(BRANCH, tip), status: IMPACT_LOG_STATUSES.rejected },
    ]);
  });

  it("a branch still on the remote → stays proposed, ledger byte-identical", async () => {
    const { ws } = await setupWorkspace();
    const tip = await pushProposal(ws, BRANCH);
    const settled = {
      ...proposedRow("skill-evolution/old", "old-tip"),
      status: IMPACT_LOG_STATUSES.accepted,
    };
    await seedLedger(ws, [settled, proposedRow(BRANCH, tip)]);
    const before = await readFile(impactLogPath(ws), "utf8");

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.updated).toBe(0);
    expect(result.classifications).toHaveLength(1);
    expect(result.classifications[0]).toMatchObject({
      branch: BRANCH,
      outcome: CLASSIFY_OUTCOMES.pending,
      status: IMPACT_LOG_STATUSES.proposed,
    });
    expect(await readFile(impactLogPath(ws), "utf8")).toBe(before);
  });

  it("a branch deleted without merging → rejected", async () => {
    const { ws, peer } = await setupWorkspace();
    const tip = await pushProposal(ws, BRANCH);
    await seedLedger(ws, [proposedRow(BRANCH, tip)]);

    await runGit(peer, ["push", "origin", "--delete", BRANCH]);

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.updated).toBe(1);
    expect(result.classifications[0]).toMatchObject({
      branch: BRANCH,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
    });
  });

  it("a tip the workspace never had (unresolvable object) → rejected via the error exit", async () => {
    const { ws } = await setupWorkspace();
    const bogus = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef";
    await seedLedger(ws, [proposedRow(BRANCH, bogus)]);

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.classifications[0]).toMatchObject({
      branch: BRANCH,
      outcome: CLASSIFY_OUTCOMES.rejected,
      status: IMPACT_LOG_STATUSES.rejected,
    });
    expect(result.classifications[0]?.reason).toMatch(/exited 128/);
  });

  it("a failed fetch (remote unreachable) → soft abort with zero row changes", async () => {
    const { ws } = await setupWorkspace();
    const tip = await pushProposal(ws, BRANCH);
    await seedLedger(ws, [proposedRow(BRANCH, tip)]);
    await runGit(ws, ["remote", "set-url", "origin", join(base, "gone.git")]);
    const before = await readFile(impactLogPath(ws), "utf8");

    const result = aborted(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.reason).toMatch(/fetch/);
    expect(await readFile(impactLogPath(ws), "utf8")).toBe(before);
    await expect(readImpactLog(impactLogPath(ws), log)).resolves.toEqual([
      proposedRow(BRANCH, tip),
    ]);
  });

  it("an empty (bootstrap-seeded) ledger → completed no-op", async () => {
    const { ws } = await setupWorkspace();
    await ensureSkillEvolutionLayout(ws, log);
    const before = await readFile(impactLogPath(ws), "utf8");

    const result = completed(
      await reconcileProposals({ workspaceRoot: ws, log, deps: gitReconcileDeps(ws) }),
    );

    expect(result.classifications).toEqual([]);
    expect(result.updated).toBe(0);
    expect(await readFile(impactLogPath(ws), "utf8")).toBe(before);
  });
});
