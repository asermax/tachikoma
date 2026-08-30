import type { GitResult } from "../../git/git.ts";
import { runGitCapture } from "../../git/git.ts";
import { listRemoteBranchTips } from "../../git/remote.ts";
import type { Logger } from "../../log.ts";
import { impactLogPath } from "./layout.ts";
import {
  BRANCH_NAMESPACE,
  REMOTE_BRANCH_PATTERN,
  type ReportedProposal,
  resolveUnderRoot,
} from "./propose.ts";
import {
  appendProposedEntry,
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
  readImpactLog,
  writeImpactLog,
} from "./store.ts";

/**
 * Post-run verification and cleanup (S8, R8/R14): the host decides what gets logged from git
 * state alone — one `ls-remote` of the proposal namespace, the remote tip SHA, and a three-dot
 * diff-scope check (`base...tip`, so only what the branch changed counts and an upstream
 * default-branch advance cannot surface as out-of-scope drift). The agent's report never creates
 * or confirms an entry: a reported-but-absent or out-of-scope branch is warned and dropped, so
 * its pattern stays eligible. The `finally` sweep is host-side and unconditional — the agent that
 * died mid-run cannot clean up after itself, and namespace sweeps need no bookkeeping about what
 * was created.
 */

/** Narrow git/store seams so verification is testable without a repository (DES-003 posture). */
export interface VerifyDeps {
  listRemoteBranchTips: (cwd: string, pattern: string) => Promise<Map<string, string>>;
  /** Files changed between the remote default branch and the tip (three-dot: merge-base..tip). */
  changedPaths: (cwd: string, base: string, tip: string) => Promise<string[]>;
  /** Absolute paths of every registered worktree, the main tree included. */
  listWorktrees: (cwd: string) => Promise<string[]>;
  removeWorktree: (cwd: string, path: string) => Promise<GitResult>;
  /** Local branch names in the proposal namespace (`skill-evolution/*`). */
  listLocalProposalBranches: (cwd: string) => Promise<string[]>;
  deleteLocalBranch: (cwd: string, name: string) => Promise<GitResult>;
  pruneWorktrees: (cwd: string) => Promise<GitResult>;
  readImpactLog: (path: string, log: Logger) => Promise<ImpactLogEntry[]>;
  writeImpactLog: (path: string, rows: readonly ImpactLogEntry[]) => Promise<void>;
}

const linesOf = (stdout: string): string[] => stdout.split("\n").filter((line) => line !== "");

/** The production dependency set over `runGitCapture` (never shell strings). */
export const gitVerifyDeps: VerifyDeps = {
  listRemoteBranchTips,

  changedPaths: async (cwd, base, tip) => {
    const result = await runGitCapture(cwd, ["diff", "--name-only", `${base}...${tip}`]);

    if (result.code !== 0) {
      throw new Error(
        `git diff --name-only ${base}...${tip} failed: ${result.stderr || `exit code ${result.code}`}`,
      );
    }

    return linesOf(result.stdout);
  },

  listWorktrees: async (cwd) => {
    const result = await runGitCapture(cwd, ["worktree", "list", "--porcelain"]);

    if (result.code !== 0) {
      throw new Error(
        `git worktree list --porcelain failed: ${result.stderr || `exit code ${result.code}`}`,
      );
    }

    // Porcelain shape: a `worktree <path>` line opens each entry.
    return linesOf(result.stdout)
      .filter((line) => line.startsWith("worktree "))
      .map((line) => line.slice("worktree ".length).trim());
  },

  removeWorktree: (cwd, path) => runGitCapture(cwd, ["worktree", "remove", "--force", path]),

  listLocalProposalBranches: async (cwd) => {
    const result = await runGitCapture(cwd, [
      "branch",
      "--list",
      `${BRANCH_NAMESPACE}*`,
      "--format=%(refname:short)",
    ]);

    return result.code === 0 ? linesOf(result.stdout) : [];
  },

  deleteLocalBranch: (cwd, name) => runGitCapture(cwd, ["branch", "-D", name]),

  pruneWorktrees: (cwd) => runGitCapture(cwd, ["worktree", "prune"]),

  readImpactLog,
  writeImpactLog,
};

/**
 * The unconditional `finally` sweep (S8): remove every worktree under the tmp dir (forced — an
 * unclean tree refuses a plain remove), then delete every local `skill-evolution/*` branch, then
 * `worktree prune`. The order is fixed: a branch checked out in a worktree blocks deletion, so
 * worktrees go first, and prune clears the administrative files for any path already gone.
 *
 * Never throws: the sweep runs inside a `finally`, where a thrown error would mask the original
 * one — each stage logs its failures and the sweep continues with whatever remains.
 */
const sweepNamespaces = async (
  workspaceRoot: string,
  tmpDir: string,
  log: Logger,
  deps: VerifyDeps,
): Promise<void> => {
  let worktrees: string[] = [];

  try {
    worktrees = (await deps.listWorktrees(workspaceRoot)).filter(
      (path) => resolveUnderRoot(tmpDir, path) != null,
    );
  } catch (error) {
    log.warn({ err: error }, "skill-evolution sweep could not list worktrees — pruning only");
  }

  for (const worktree of worktrees) {
    const result = await deps.removeWorktree(workspaceRoot, worktree);

    if (result.code !== 0) {
      log.warn(
        { path: worktree, stderr: result.stderr },
        "skill-evolution sweep failed to remove a worktree",
      );
    }
  }

  let branches: string[] = [];

  try {
    branches = await deps.listLocalProposalBranches(workspaceRoot);
  } catch (error) {
    log.warn({ err: error }, "skill-evolution sweep could not list local proposal branches");
  }

  for (const branch of branches) {
    const result = await deps.deleteLocalBranch(workspaceRoot, branch);

    if (result.code !== 0) {
      log.warn(
        { branch, stderr: result.stderr },
        "skill-evolution sweep failed to delete a local branch",
      );
    }
  }

  const pruned = await deps.pruneWorktrees(workspaceRoot);

  if (pruned.code !== 0) {
    log.warn({ stderr: pruned.stderr }, "skill-evolution sweep failed to prune worktrees");
  }

  log.debug(
    { removedWorktrees: worktrees.length, deletedBranches: branches.length },
    "skill-evolution namespace sweep done",
  );
};

export interface VerifyDepsInput {
  workspaceRoot: string;
  tmpDir: string;
  /** The agent's `report_proposals` list — claims to verify, never facts to record. */
  reported: readonly ReportedProposal[];
  defaultBranch: string;
  /** Injects the ledger row's date — the trunk-close clock, not wall-clock. */
  now: () => Date;
  log: Logger;
  deps: VerifyDeps;
}

/**
 * Verify each reported proposal from git state, append `proposed` rows for what holds, and sweep
 * the feature's namespaces no matter how the run ended. Returns the appended rows (possibly
 * empty) — the reporter fires only on a non-empty result. A verification-stage throw (e.g. the
 * remote listing fails) propagates to the processor's fail-soft boundary, but only AFTER the
 * `finally` sweep has removed every worktree and local branch.
 */
export const verifyAndRecord = async (input: VerifyDepsInput): Promise<ImpactLogEntry[]> => {
  const { workspaceRoot, tmpDir, reported, defaultBranch, now, log, deps } = input;
  const day = now().toISOString().slice(0, 10);

  try {
    // One namespace scan is the authoritative remote view; everything below reads it.
    const remoteTips = await deps.listRemoteBranchTips(workspaceRoot, REMOTE_BRANCH_PATTERN);
    const ledgerPath = impactLogPath(workspaceRoot);
    let rows = await deps.readImpactLog(ledgerPath, log);
    const verified: ImpactLogEntry[] = [];
    const seenBranches = new Set<string>();

    for (const proposal of reported) {
      if (seenBranches.has(proposal.branch)) {
        log.warn({ branch: proposal.branch }, "proposal reported twice — recording it once");
        continue;
      }
      seenBranches.add(proposal.branch);

      const tip = remoteTips.get(proposal.branch);

      if (tip == null) {
        log.warn(
          { branch: proposal.branch },
          "reported proposal branch is absent on the remote — not logged; the pattern stays eligible",
        );
        continue;
      }

      let changed: string[];

      try {
        changed = await deps.changedPaths(
          workspaceRoot,
          `refs/remotes/origin/${defaultBranch}`,
          tip,
        );
      } catch (error) {
        log.warn(
          { branch: proposal.branch, err: error },
          "proposal diff-scope check failed — not logged; the pattern stays eligible",
        );
        continue;
      }

      const outOfScope = changed.filter((path) => !path.startsWith("skills/"));

      if (outOfScope.length > 0) {
        log.warn(
          { branch: proposal.branch, outOfScope },
          "proposal touches files outside skills/ — not logged; the pattern stays eligible",
        );
        continue;
      }

      const entry = {
        date: day,
        skill: proposal.skill,
        pattern: proposal.pattern,
        branch: proposal.branch,
        tip,
        description: proposal.description,
      };

      rows = appendProposedEntry(rows, entry);
      verified.push({ ...entry, status: IMPACT_LOG_STATUSES.proposed });
      log.info({ branch: proposal.branch, tip }, "skill-evolution proposal verified and logged");
    }

    if (verified.length > 0) {
      await deps.writeImpactLog(ledgerPath, rows);
    }

    return verified;
  } finally {
    await sweepNamespaces(workspaceRoot, tmpDir, log, deps);
  }
};
