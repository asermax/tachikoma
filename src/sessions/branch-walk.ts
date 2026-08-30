import { rm } from "node:fs/promises";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import type { AgentManager } from "../agent/manager.ts";
import type { Logger } from "../log.ts";
import type { BranchRecord } from "./trunk.ts";

/**
 * Caller-shaped per-branch progress lines. Memory's three templates ("Extracting branch i/n…",
 * "Extracted branch i/n", "Branch i/n failed — will retry") and skill-evolution's ("Analyzing
 * skills — branch i/n") are different shapes, so a single label string cannot reproduce them.
 * `start`/`done` bracket a completed body; `failed` fires only when a branch errors.
 */
export interface BranchWalkProgress {
  start: (i: number, n: number) => string;
  done: (i: number, n: number) => string;
  failed: (i: number, n: number) => string;
}

/**
 * The neutral walk skeleton shared by memory extraction and skill-evolution analysis (DLT-080 R2).
 * The walk owns the loop — skip-if-marked, the branch-file cut, per-branch error isolation,
 * settle-then-delete cleanup, the completion marker, and progress lines — while the body and the
 * marker key stay caller-supplied: memory's pair matches `record.branchId`, skill-evolution's
 * matches `record.summaryEntryId` (stable under `/rollback` renumbering).
 */
export interface BranchWalkDeps {
  agent: Pick<AgentManager, "forkAndContinue" | "branchFile">;
  /** The caller's per-branch work (forks, sweeps) over the cut branch file. */
  body: (record: BranchRecord, branchFilePath: string) => Promise<void>;
  isDone: (session: AgentSession, record: BranchRecord) => boolean;
  markDone: (session: AgentSession, record: BranchRecord) => void;
  log: Logger;
  /** Raw status surface for any caller-shaped line (the processor's `status` callback). */
  status?: (text: string) => void;
  /** Caller-shaped per-branch progress lines (see {@link BranchWalkProgress}). */
  progress?: BranchWalkProgress;
}

/**
 * Walk every record serially: skip records already marked done, cut each branch into its own
 * throwaway session file, run the caller's body to settlement, delete the file, then write the
 * completion marker. A branch whose cut fails is warned and skipped (not a failure — nothing ran);
 * a branch whose body throws is isolated into the returned `failed` list so each caller picks its
 * own failure semantics (memory throws to keep the trunk unclosed; skill-evolution logs and
 * continues).
 *
 * Concurrent walkers over the same trunk session file are safe by construction: every
 * `agent.branchFile` cut opens a detached `SessionManager` (see `AgentManager.branchFile`), so one
 * walker's cleanup never touches another walker's branch file.
 */
export const walkBranches = async (
  session: AgentSession,
  sessionFile: string,
  records: BranchRecord[],
  day: string,
  deps: BranchWalkDeps,
): Promise<{ failed: BranchRecord[] }> => {
  const failed: BranchRecord[] = [];
  const n = records.length;

  for (const [i0, record] of records.entries()) {
    // 1-based array position over ALL records (advances even for skipped branches, so a recovery
    // re-run still shows each branch's true position in the day's set).
    const i = i0 + 1;

    if (deps.isDone(session, record)) {
      deps.log.debug({ day, branchId: record.branchId }, "branch already done — skipping");
      continue;
    }

    // Cut the branch from a manager loaded fresh off disk — NOT the live trunk session — so this
    // never mutates the session whose other branches we still need to walk (see AgentManager.branchFile).
    const branchFilePath = deps.agent.branchFile(sessionFile, record.originalLeafId);

    if (branchFilePath == null) {
      deps.log.warn(
        { day, branchId: record.branchId },
        "could not cut branch file — skipping branch",
      );
      continue;
    }

    if (deps.progress != null) deps.status?.(deps.progress.start(i, n));

    const start = Date.now();

    try {
      try {
        await deps.body(record, branchFilePath);
      } finally {
        // Delete only after the body settles: the body may run sibling forks that are still
        // copying the shared branch file — tearing it down mid-read would corrupt them.
        await rm(branchFilePath, { force: true });
      }

      deps.markDone(session, record);

      if (deps.progress != null) deps.status?.(deps.progress.done(i, n));

      deps.log.info(
        { day, branchId: record.branchId, durationMs: Date.now() - start },
        "branch walk completed",
      );
    } catch (error) {
      failed.push(record);
      if (deps.progress != null) deps.status?.(deps.progress.failed(i, n));
      deps.log.error({ day, err: error, branchId: record.branchId }, "branch failed — will retry");
    }
  }

  return { failed };
};
