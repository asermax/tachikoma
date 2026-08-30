import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { AgentManager } from "../../agent/manager.ts";
import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import { walkBranches } from "../../sessions/branch-walk.ts";
import { type BranchRecord, isBranchAnalyzed, markBranchAnalyzed } from "../../sessions/trunk.ts";
import { sweepEmptyMarkdown } from "../../util/markdown-store.ts";
import { skillEvolutionDir } from "./layout.ts";
import { branchAnalysisInstruction, maintenanceSystemPrompt } from "./prompts.ts";
import { IMPACT_LOG_FILENAME } from "./store.ts";

/** Used by the maintenance pass, which runs a bare headless side-run (memory's `Runner` idiom). */
export type Runner = Pick<SideRunner, "run">;

/** The slice of AgentManager the analysis body needs: cut a branch file, then fork its conversation. */
export type BranchForker = Pick<AgentManager, "forkAndContinue" | "branchFile">;

export interface AnalysisDeps {
  agent: BranchForker;
  workspaceRoot: string;
  log: Logger;
  /** Per-close progress surface — the processor's `status` callback, threaded through the walk. */
  status?: (text: string) => void;
}

/**
 * The per-branch evidence collection (S5, R3/R4/R12): analyze each unanalyzed topic branch on the
 * shared walk. The body is one conversation-aware fork — `forkAndContinue` under `FILE_EDIT_TOOLS`,
 * so the branch's turns are live in the analyzing agent's context (that is where failed invocations
 * and workarounds actually show; replaying the transcript as text would lose them) — followed by a
 * host sweep of the store (the fork empties merged-away pages; the host removes the leftovers).
 *
 * The marker pair is keyed by `record.summaryEntryId`, not the positional `topic-N` branch id — a
 * `/rollback` reversal renumbers the topic set, and the crash-recovery re-close is exactly when that
 * would bite (DES-008's entry-ids-not-positions rule). At most once per branch; a re-run close
 * skips marked branches for free.
 *
 * Fail-soft by construction (R11): the walk isolates a failing branch into the returned list and
 * this function never throws — the CALLER decides what a failure means (memory throws to keep the
 * trunk unclosed; skill-evolution logs and continues).
 */
export const analyzeBranches = async (
  session: AgentSession,
  sessionFile: string,
  records: BranchRecord[],
  day: string,
  deps: AnalysisDeps,
): Promise<{ failed: BranchRecord[] }> => {
  const { agent, workspaceRoot, log } = deps;

  const body = async (record: BranchRecord, branchFilePath: string): Promise<void> => {
    await agent.forkAndContinue(
      branchFilePath,
      branchAnalysisInstruction(workspaceRoot, record, day),
      "processor",
      FILE_EDIT_TOOLS,
    );

    // The ledger is host-written bookkeeping — a blanked one must survive the sweep (the index
    // always does; the sweep's own structural rule).
    await sweepEmptyMarkdown(skillEvolutionDir(workspaceRoot), log, [IMPACT_LOG_FILENAME]);
  };

  return walkBranches(session, sessionFile, records, day, {
    agent,
    body,
    isDone: (trunkSession, record) => isBranchAnalyzed(trunkSession, record.summaryEntryId),
    markDone: (trunkSession, record) => markBranchAnalyzed(trunkSession, record.summaryEntryId),
    log,
    status: deps.status,
    progress: {
      start: (i, n) => `Analyzing skills — branch ${i}/${n}…`,
      done: (i, n) => `Analyzed skills — branch ${i}/${n}`,
      failed: (i, n) => `Skill analysis failed — branch ${i}/${n}`,
    },
  });
};

export interface MaintenanceDeps {
  side: Runner;
  workspaceRoot: string;
  log: Logger;
}

/**
 * The in-run maintenance pass (R6b): a context-free headless run over the store — merge
 * near-duplicate patterns, enforce the ~50-line caps, empty superseded pages — followed by a host
 * sweep that removes what it emptied. Memory's `runStoreMaintenance` shape, minus the commit: this
 * extension never commits its own store writes — the finalize-phase workspace `git-commit`
 * processor is the backstop, and memory's mid-close `commitAll` may already sweep up part of the
 * pass (writes converge at the finalize commit; only commit grouping splits).
 */
export const runMaintenance = async (deps: MaintenanceDeps): Promise<void> => {
  deps.log.info("skill-evolution maintenance pass started");

  const start = Date.now();

  const result = await deps.side.run({
    tools: FILE_EDIT_TOOLS,
    system: maintenanceSystemPrompt(deps.workspaceRoot),
    prompt: "Perform the maintenance pass now, following your instructions.",
    tier: "processor",
  });

  await sweepEmptyMarkdown(skillEvolutionDir(deps.workspaceRoot), deps.log, [IMPACT_LOG_FILENAME]);

  deps.log.info(
    { producedOutput: result.text.length > 0, durationMs: Date.now() - start },
    "skill-evolution maintenance pass completed",
  );
};
