import type { GitResult } from "../../git/git.ts";
import { runGitCapture } from "../../git/git.ts";
import { fetchRemote, resolveRemoteDefaultBranch } from "../../git/remote.ts";
import type { Logger } from "../../log.ts";
import { impactLogPath } from "./layout.ts";
import {
  IMPACT_LOG_STATUSES,
  type ImpactLogEntry,
  type ImpactLogStatus,
  readImpactLog,
  updateEntryStatus,
  writeImpactLog,
} from "./store.ts";

/**
 * Reconciliation (S4, R1): the first stage of every run. Refresh remote state with the run's own
 * `fetch --prune` (nothing else in the same close fetches the workspace), then classify every
 * `proposed` ledger row before any analysis runs. Both remote-facing failures soft-abort with
 * nothing classified — classifying against stale refs or an unresolved default branch would
 * mass-reject open proposals irreversibly, the one outcome the abort exists to prevent.
 */

/** Classification outcomes (S4). `pending` is the holding state: the row stays `proposed`. */
export const CLASSIFY_OUTCOMES = {
  accepted: "accepted",
  pending: "pending",
  rejected: "rejected",
} as const;

export type ClassifyOutcome = (typeof CLASSIFY_OUTCOMES)[keyof typeof CLASSIFY_OUTCOMES];

/** What one proposed row resolved to — every ladder path returns one, so the run log tells the story. */
export interface ClassificationRecord {
  /** The ledger row's stable key. */
  branch: string;
  tip: string;
  outcome: ClassifyOutcome;
  /** The status to write back: pending keeps `proposed` (no change). */
  status: ImpactLogStatus;
  /** One-line why, for the run log. */
  reason: string;
}

/** Narrow git seams so the ladder is unit-testable without a repository. */
export interface ClassifyDeps {
  /** The ref reachability is judged against: `refs/remotes/origin/<default>`. */
  defaultRemoteRef: string;
  /**
   * `git merge-base --is-ancestor <tip> <defaultRemoteRef>` exit code: 0 = ancestor, 1 = not,
   * anything else = error (e.g. 128 on an unresolvable object after a squash merge and GC).
   */
  isAncestor: (tip: string, defaultRemoteRef: string) => Promise<number>;
  /** Whether `refs/remotes/origin/<branch>` survives the prune fetch — the branch-still-open signal. */
  trackingRefExists: (branch: string) => Promise<boolean>;
}

/**
 * The classification ladder, reachability-first (R1): a reachable tip is accepted whether or not
 * the branch still exists; an unreachable tip falls back to branch presence; an errored probe is
 * rejected — a missing object cannot have been merge-committed (a merge keeps it reachable), so
 * "other" is the documented squash-merge outcome.
 */
export const classify = async (
  entry: Pick<ImpactLogEntry, "branch" | "tip">,
  deps: ClassifyDeps,
): Promise<ClassificationRecord> => {
  const exit = await deps.isAncestor(entry.tip, deps.defaultRemoteRef);

  if (exit === 0) {
    return {
      branch: entry.branch,
      tip: entry.tip,
      outcome: CLASSIFY_OUTCOMES.accepted,
      status: IMPACT_LOG_STATUSES.accepted,
      reason: `tip is reachable from ${deps.defaultRemoteRef} — merged`,
    };
  }

  if (exit === 1) {
    const present = await deps.trackingRefExists(entry.branch);

    return present
      ? {
          branch: entry.branch,
          tip: entry.tip,
          outcome: CLASSIFY_OUTCOMES.pending,
          status: IMPACT_LOG_STATUSES.proposed,
          reason: "tip not merged yet and the branch is still on the remote — stays proposed",
        }
      : {
          branch: entry.branch,
          tip: entry.tip,
          outcome: CLASSIFY_OUTCOMES.rejected,
          status: IMPACT_LOG_STATUSES.rejected,
          reason: "tip never landed and the branch is gone from the remote",
        };
  }

  return {
    branch: entry.branch,
    tip: entry.tip,
    outcome: CLASSIFY_OUTCOMES.rejected,
    status: IMPACT_LOG_STATUSES.rejected,
    reason: `merge-base --is-ancestor exited ${exit} (unresolvable object, e.g. post-squash-merge GC)`,
  };
};

/** A soft abort (R1): the caller skips the whole run — nothing was classified or written. */
export interface ReconcileAborted {
  aborted: true;
  /** Why — the processor logs and notifies this. */
  reason: string;
}

export interface ReconcileCompleted {
  aborted: false;
  /** One record per classified `proposed` row, in ledger order. */
  classifications: ClassificationRecord[];
  /** Rows whose status actually changed (pending rows stay `proposed` and don't count). */
  updated: number;
}

export type ReconcileResult = ReconcileAborted | ReconcileCompleted;

/** Everything reconcileProposals needs from git and the store — injected (DES-003 fake posture). */
export interface ReconcileDeps {
  fetchRemote: (cwd: string) => Promise<GitResult>;
  resolveRemoteDefaultBranch: (cwd: string) => Promise<string>;
  isAncestor: (tip: string, defaultRemoteRef: string) => Promise<number>;
  trackingRefExists: (branch: string) => Promise<boolean>;
  readImpactLog: (path: string, log: Logger) => Promise<ImpactLogEntry[]>;
  writeImpactLog: (path: string, rows: readonly ImpactLogEntry[]) => Promise<void>;
}

/** The production dependency set, bound to the workspace checkout. */
export const gitReconcileDeps = (cwd: string): ReconcileDeps => ({
  fetchRemote,
  resolveRemoteDefaultBranch,
  isAncestor: async (tip, defaultRemoteRef) =>
    (await runGitCapture(cwd, ["merge-base", "--is-ancestor", tip, defaultRemoteRef])).code,
  trackingRefExists: async (branch) =>
    (
      await runGitCapture(cwd, [
        "rev-parse",
        "--verify",
        "--quiet",
        `refs/remotes/origin/${branch}`,
      ])
    ).code === 0,
  readImpactLog,
  writeImpactLog,
});

/**
 * Fetch, resolve the default branch, then classify each `proposed` row and write the changed
 * statuses back. Malformed rows never arrive — the store layer already warned and skipped them.
 * Never throws: every remote-facing failure is a returned soft-abort.
 */
export const reconcileProposals = async (input: {
  workspaceRoot: string;
  log: Logger;
  deps: ReconcileDeps;
}): Promise<ReconcileResult> => {
  const { workspaceRoot, log, deps } = input;

  // The run's own fetch: `--prune` is what makes an absent tracking ref mean "deleted on the
  // remote", so the branch-presence probe judges fresh state. A failure aborts before anything
  // else — analyzing and classifying against stale refs is what R1 bars.
  const fetch = await deps.fetchRemote(workspaceRoot);
  if (fetch.code !== 0) {
    return {
      aborted: true,
      reason: `reconciliation aborted: git fetch origin --prune failed (exit ${fetch.code})${
        fetch.stderr === "" ? "" : `: ${fetch.stderr}`
      }`,
    };
  }

  // The ref every classification judges against. Unresolved → abort before classification:
  // judging against a wrong ref would flip every open row to rejected, permanently.
  let defaultBranch: string;
  try {
    defaultBranch = await deps.resolveRemoteDefaultBranch(workspaceRoot);
  } catch (error) {
    return {
      aborted: true,
      reason: `reconciliation aborted: ${(error as Error).message}`,
    };
  }

  const path = impactLogPath(workspaceRoot);
  const rows = await deps.readImpactLog(path, log);
  const classifyDeps: ClassifyDeps = {
    defaultRemoteRef: `refs/remotes/origin/${defaultBranch}`,
    isAncestor: deps.isAncestor,
    trackingRefExists: deps.trackingRefExists,
  };

  const classifications: ClassificationRecord[] = [];
  let updatedRows = rows;
  let updated = 0;

  for (const row of rows) {
    if (row.status !== IMPACT_LOG_STATUSES.proposed) continue;

    const record = await classify(row, classifyDeps);
    classifications.push(record);
    log.info(
      { branch: record.branch, tip: record.tip, outcome: record.outcome, reason: record.reason },
      "skill-evolution proposal classified",
    );

    if (record.status !== row.status) {
      updatedRows = updateEntryStatus(updatedRows, row.branch, row.tip, record.status);
      updated += 1;
    }
  }

  // Write back only when something changed — a fully-pending or empty ledger run is a byte-level
  // no-op, and the finalize commit has nothing extra to pick up.
  if (updated > 0) {
    await deps.writeImpactLog(path, updatedRows);
  }

  return { aborted: false, classifications, updated };
};
