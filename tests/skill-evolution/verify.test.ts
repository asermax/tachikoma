import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ensureSkillEvolutionLayout,
  impactLogPath,
  skillEvolutionDir,
} from "../../src/extensions/skill-evolution/layout.ts";
import type { ReportedProposal } from "../../src/extensions/skill-evolution/propose.ts";
import {
  IMPACT_LOG_STATUSES,
  readImpactLog,
  writeImpactLog,
} from "../../src/extensions/skill-evolution/store.ts";
import {
  gitVerifyDeps,
  type VerifyDeps,
  verifyAndRecord,
} from "../../src/extensions/skill-evolution/verify.ts";
import { runGit } from "../../src/git/git.ts";
import { listRemoteBranchTips } from "../../src/git/remote.ts";
import { fakeLogger, makeTempDir, setupRemotePair } from "../git/helpers.ts";

const NOW = () => new Date("2027-03-04T05:00:00Z");
const DAY = "2027-03-04";

const reported = (branch: string): ReportedProposal => ({
  branch,
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  description: "Add the --env flag to the deploy guidance",
});

describe("verifyAndRecord (real git, bare origin)", () => {
  let base: string;
  let ws: string;
  let tmpDir: string;

  beforeEach(async () => {
    base = await makeTempDir();
    ws = (await setupRemotePair(base)).cloneA;
    tmpDir = join(ws, ".tachikoma", "tmp", "skill-evolution");
    // Production workspaces ignore `.tachikoma/` (GITIGNORE_ENTRIES) — the tmp
    // worktrees must never surface in the main tree's status.
    await writeFile(join(ws, ".gitignore"), ".tachikoma/\n", "utf8");
    await mkdir(tmpDir, { recursive: true });

    // A committed store (what the finalize phase leaves behind) plus one workspace skill, so the
    // main tree starts clean and in-scope edits are possible.
    await mkdir(join(ws, "skills", "deploy"), { recursive: true });
    await writeFile(join(ws, "skills", "deploy", "SKILL.md"), "# Deploy\n\nGuidance.\n", "utf8");
    await ensureSkillEvolutionLayout(ws, fakeLogger());
    await runGit(ws, ["add", "-A"]);
    await runGit(ws, ["commit", "-m", "Seed skills and store"]);
    await runGit(ws, ["push", "origin", "main"]);
  });

  afterEach(async () => {
    await runGit(ws, ["worktree", "prune"]);
    await rm(base, { recursive: true, force: true });
  });

  /**
   * Author a proposal branch the way the agent does: a worktree under the tmp dir cut from the
   * remote default branch, one edit, a commit, a push. `edit` receives the worktree path.
   */
  const authorProposal = async (
    branch: string,
    edit: (worktree: string) => Promise<void>,
  ): Promise<string> => {
    const slug = branch.split("/")[1] ?? branch;
    const worktree = join(tmpDir, slug);

    await runGit(ws, ["worktree", "add", "-b", branch, worktree, "refs/remotes/origin/main"]);
    await edit(worktree);
    await runGit(worktree, ["add", "-A"]);
    await runGit(worktree, ["-c", "core.editor=true", "commit", "-m", `Propose ${branch}`]);
    await runGit(worktree, ["push", "origin", branch]);

    return worktree;
  };

  const worktreesUnderTmp = async (): Promise<string[]> =>
    (await gitVerifyDeps.listWorktrees(ws)).filter((path) => path.startsWith(tmpDir));

  const localProposalBranches = async (): Promise<string[]> =>
    gitVerifyDeps.listLocalProposalBranches(ws);

  const runVerify = (over: {
    reported?: ReportedProposal[];
    deps?: Partial<VerifyDeps>;
  }): Promise<ReturnType<typeof verifyAndRecord>> =>
    verifyAndRecord({
      workspaceRoot: ws,
      tmpDir,
      defaultBranch: "main",
      reported: over.reported ?? [],
      now: NOW,
      log: fakeLogger(),
      deps: { ...gitVerifyDeps, ...over.deps },
    });

  it("a pushed in-scope proposal is logged with the remote tip SHA and swept", async () => {
    await authorProposal("skill-evolution/deploy-env-flag", async (worktree) => {
      await writeFile(
        join(worktree, "skills", "deploy", "SKILL.md"),
        "# Deploy\n\n--env flag.\n",
        "utf8",
      );
    });

    const verified = await runVerify({ reported: [reported("skill-evolution/deploy-env-flag")] });

    const remoteTip = (await listRemoteBranchTips(ws, "skill-evolution/*")).get(
      "skill-evolution/deploy-env-flag",
    );

    expect(verified).toEqual([
      {
        date: DAY,
        skill: "deploy",
        pattern: "deploy-env-flag.md",
        branch: "skill-evolution/deploy-env-flag",
        tip: remoteTip,
        description: "Add the --env flag to the deploy guidance",
        status: IMPACT_LOG_STATUSES.proposed,
      },
    ]);

    await expect(readImpactLog(impactLogPath(ws), fakeLogger())).resolves.toEqual(verified);

    // The sweep: no worktree under the tmp dir, no local proposal branch.
    await expect(worktreesUnderTmp()).resolves.toEqual([]);
    await expect(localProposalBranches()).resolves.toEqual([]);

    // The main tree is untouched by the proposal machinery (the ledger row is expected output —
    // the finalize commit's job, mirrored here).
    await runGit(ws, ["add", "-A"]);
    await runGit(ws, ["commit", "-m", "Record proposal"]);
    expect(await runGit(ws, ["status", "--porcelain"])).toBe("");
  });

  it("the same branch reported twice is logged once", async () => {
    await authorProposal("skill-evolution/deploy-env-flag", async (worktree) => {
      await writeFile(
        join(worktree, "skills", "deploy", "SKILL.md"),
        "# Deploy\n\n--env flag.\n",
        "utf8",
      );
    });

    const verified = await runVerify({
      reported: [
        reported("skill-evolution/deploy-env-flag"),
        reported("skill-evolution/deploy-env-flag"),
      ],
    });

    expect(verified).toHaveLength(1);
    await expect(readImpactLog(impactLogPath(ws), fakeLogger())).resolves.toHaveLength(1);
  });

  it("a proposal touching README.md is dropped with a warning — no row", async () => {
    await authorProposal("skill-evolution/deploy-env-flag", async (worktree) => {
      await writeFile(join(worktree, "README.md"), "docs\n", "utf8");
    });

    const log = fakeLogger();
    const verified = await verifyAndRecord({
      workspaceRoot: ws,
      tmpDir,
      defaultBranch: "main",
      reported: [reported("skill-evolution/deploy-env-flag")],
      now: NOW,
      log,
      deps: gitVerifyDeps,
    });

    expect(verified).toEqual([]);
    await expect(readImpactLog(impactLogPath(ws), fakeLogger())).resolves.toEqual([]);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ branch: "skill-evolution/deploy-env-flag" }),
      expect.stringContaining("outside skills/"),
    );
    // Nothing was written, so the main tree stays byte-identical.
    expect(await runGit(ws, ["status", "--porcelain"])).toBe("");
  });

  it("a reported branch absent from the remote is dropped with a warning — no row", async () => {
    const log = fakeLogger();
    const verified = await verifyAndRecord({
      workspaceRoot: ws,
      tmpDir,
      defaultBranch: "main",
      reported: [reported("skill-evolution/ghost")],
      now: NOW,
      log,
      deps: gitVerifyDeps,
    });

    expect(verified).toEqual([]);
    await expect(readImpactLog(impactLogPath(ws), fakeLogger())).resolves.toEqual([]);
    expect(log.warn).toHaveBeenCalledWith(
      expect.objectContaining({ branch: "skill-evolution/ghost" }),
      expect.stringContaining("absent on the remote"),
    );
  });

  it("an out-of-order death (verification throws) still sweeps every worktree and branch", async () => {
    // The agent died after `worktree add` but before any push.
    const worktree = join(tmpDir, "dead-branch");
    await runGit(ws, [
      "worktree",
      "add",
      "-b",
      "skill-evolution/dead-branch",
      worktree,
      "refs/remotes/origin/main",
    ]);

    const boom: VerifyDeps = {
      ...gitVerifyDeps,
      listRemoteBranchTips: vi.fn(async () => {
        throw new Error("remote listing failed");
      }),
    };

    await expect(
      verifyAndRecord({
        workspaceRoot: ws,
        tmpDir,
        defaultBranch: "main",
        reported: [reported("skill-evolution/dead-branch")],
        now: NOW,
        log: fakeLogger(),
        deps: boom,
      }),
    ).rejects.toThrow("remote listing failed");

    await expect(worktreesUnderTmp()).resolves.toEqual([]);
    await expect(localProposalBranches()).resolves.toEqual([]);
  });

  it("an unclean worktree is force-removed by the sweep", async () => {
    const worktree = join(tmpDir, "dirty");
    await runGit(ws, [
      "worktree",
      "add",
      "-b",
      "skill-evolution/dirty",
      worktree,
      "refs/remotes/origin/main",
    ]);
    await writeFile(join(worktree, "skills", "deploy", "SKILL.md"), "uncommitted edit\n", "utf8");

    // A plain (unforced) remove would refuse an unclean tree; the sweep forces.
    await expect(runVerify({ reported: [] })).resolves.toEqual([]);

    await expect(worktreesUnderTmp()).resolves.toEqual([]);
    await expect(localProposalBranches()).resolves.toEqual([]);
    expect(await runGit(ws, ["status", "--porcelain"])).toBe("");
  });

  it("the sweep also clears a worktree whose directory was deleted out from under git", async () => {
    const worktree = join(tmpDir, "vanished");
    await runGit(ws, [
      "worktree",
      "add",
      "-b",
      "skill-evolution/vanished",
      worktree,
      "refs/remotes/origin/main",
    ]);
    await rm(worktree, { recursive: true, force: true });

    await expect(runVerify({ reported: [] })).resolves.toEqual([]);

    // prune cleared the administrative entry: no stale worktree registration, no local branch.
    await expect(localProposalBranches()).resolves.toEqual([]);
    const stale = (await gitVerifyDeps.listWorktrees(ws)).filter((path) =>
      path.includes("vanished"),
    );
    expect(stale).toEqual([]);
  });

  it("the impact-log write never happens when nothing verified — the file stays byte-identical", async () => {
    const before = await readFile(impactLogPath(ws), "utf8");

    await runVerify({ reported: [reported("skill-evolution/ghost")] });

    expect(await readFile(impactLogPath(ws), "utf8")).toBe(before);
  });

  it("existing settled rows survive an append", async () => {
    const settled = {
      date: "2026-08-01",
      skill: "commit",
      pattern: "commit-msg.md",
      branch: "skill-evolution/commit-msg",
      tip: "abc123def456abc123def456abc123def456abc1",
      description: "Tighten the message guidance",
      status: IMPACT_LOG_STATUSES.accepted,
    };
    await writeImpactLog(impactLogPath(ws), [settled]);

    await authorProposal("skill-evolution/deploy-env-flag", async (worktree) => {
      await writeFile(
        join(worktree, "skills", "deploy", "SKILL.md"),
        "# Deploy\n\n--env flag.\n",
        "utf8",
      );
    });
    await runVerify({ reported: [reported("skill-evolution/deploy-env-flag")] });

    const rows = await readImpactLog(impactLogPath(ws), fakeLogger());

    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual(settled);
    expect(rows[1]).toMatchObject({
      branch: "skill-evolution/deploy-env-flag",
      status: IMPACT_LOG_STATUSES.proposed,
    });
  });

  it("never touches the pattern pages on disk (the ledger is the only write)", async () => {
    const page = join(skillEvolutionDir(ws), "deploy-env-flag.md");
    await writeFile(page, "# deploy-env-flag\n\n## Problem\n--env flag missing.\n", "utf8");
    await runGit(ws, ["add", "-A"]);
    await runGit(ws, ["commit", "-m", "Add pattern page"]);

    await authorProposal("skill-evolution/deploy-env-flag", async (worktree) => {
      await writeFile(
        join(worktree, "skills", "deploy", "SKILL.md"),
        "# Deploy\n\n--env flag.\n",
        "utf8",
      );
    });
    await runVerify({ reported: [reported("skill-evolution/deploy-env-flag")] });

    expect(await readFile(page, "utf8")).toBe(
      "# deploy-env-flag\n\n## Problem\n--env flag missing.\n",
    );
  });
});
