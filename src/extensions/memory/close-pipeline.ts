import { rm } from "node:fs/promises";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { AgentManager } from "../../agent/manager.ts";
import { createBranchFile } from "../../agent/session-tree.ts";
import type { Logger } from "../../log.ts";
import {
  type BranchRecord,
  isBranchExtracted,
  isStepDone,
  markBranchExtracted,
  markStepDone,
} from "../../sessions/trunk.ts";
import type { PostProcessor } from "../api.ts";
import type { Runner } from "./extraction.ts";
import { storeInstruction } from "./extraction.ts";
import { MEMORY_STORES, type MemoryStore, storeDir, sweepEmptyMarkdown } from "./layout.ts";
import { contextMaintenanceSystemPrompt, maintenanceSystemPrompt } from "./maintenance.ts";

/**
 * The trunk-close memory pipeline. Closing the day's trunk runs a phased, idempotent
 * pipeline keyed on the day's branches:
 *   1. per-branch extraction  (per-branch `extracted` marker)
 *   2. prune                  (per-step marker)
 *   3. consolidation          (per-step marker)
 * plus the once-daily core-context update step (per-step marker).
 *
 * This module is the PLUGGABLE SEAM: the interim phase bodies REUSE the existing
 * `maintenance.ts` logic; a later memory-consolidation change replaces the prune/consolidation/core-context
 * bodies with staged/atomic store-mutation versions behind the SAME exported functions. The trigger, ordering, and
 * marker machinery (the commit-with-marker ordering: body → marker) stay here regardless.
 *
 * Markers are written ONLY after a phase body completes, so a crash before a marker re-runs that phase
 * cleanly on the next close/recovery (the markers are persisted on the session file as custom entries).
 */

/** Per-step completion-marker names; one per ordered close-pipeline step after extraction. */
export const CLOSE_STEPS = {
  prune: "memory-prune",
  consolidate: "memory-consolidate",
  coreContext: "core-context-update",
} as const;

export type CloseStep = (typeof CLOSE_STEPS)[keyof typeof CLOSE_STEPS];

/** The slice of AgentManager per-branch extraction needs: fork a single branch's conversation. */
export type BranchForker = Pick<AgentManager, "forkAndContinue">;

export interface CloseExtractionDeps {
  agent: BranchForker;
  workspaceRoot: string;
}

export interface ClosePhaseDeps {
  side: Runner;
  workspaceRoot: string;
  settings: {
    recentDays: number;
    weeklyThresholdMonths: number;
    monthlyThresholdMonths: number;
  };
  log: Logger;
  now?: () => Date;
  /** Commit the files a phase touched (so the staged result is durable alongside its marker). */
  commitChanges?: (message: string) => Promise<void>;
}

/**
 * Phase 1 — per-branch extraction. For every branch lacking an `extracted` marker, fork ONLY that
 * branch's conversation (root → its original leaf, sliced conceptually from its base forward) and run
 * the three store extractions over it, then write the per-branch marker. Idempotent: a re-run skips
 * branches that already carry a marker. The temp branch file is deleted after the fork.
 */
export const extractBranches = async (
  session: AgentSession,
  records: BranchRecord[],
  { agent, workspaceRoot }: CloseExtractionDeps,
  log: Logger,
): Promise<void> => {
  for (const record of records) {
    if (isBranchExtracted(session, record.branchId)) {
      log.debug({ branchId: record.branchId }, "branch already extracted — skipping");
      continue;
    }

    const branchFile = createBranchFile(session, record.originalLeafId);

    if (branchFile == null) {
      log.warn({ branchId: record.branchId }, "could not fork branch for extraction — skipping");
      continue;
    }

    const start = Date.now();

    try {
      for (const store of MEMORY_STORES) {
        // Hard-limit the fork to file tools — the extraction agent reuses the session's persona but
        // must not message the user or fire tasks (belt-and-suspenders with SILENT_BACKGROUND_SECTION).
        await agent.forkAndContinue(
          branchFile,
          branchStoreInstruction(store, workspaceRoot, record),
          "processor",
          FILE_EDIT_TOOLS,
        );

        await sweepEmptyMarkdown(storeDir(workspaceRoot, store), log);
      }
    } finally {
      await rm(branchFile, { force: true });
    }

    markBranchExtracted(session, record.branchId);

    log.info({ branchId: record.branchId, durationMs: Date.now() - start }, "branch extracted");
  }
};

/** The follow-up instruction for one store, noting the fork is a single topic branch's own turns. */
const branchStoreInstruction = (
  store: MemoryStore,
  workspaceRoot: string,
  record: BranchRecord,
): string =>
  `${storeInstruction(store, workspaceRoot)}\n\nThis conversation is a single topic branch (\`${record.branchId}\`) from today's session. Focus only on this branch's own turns.`;

/**
 * Phase 2 — prune. Interim body: run the existing per-store maintenance tick (prune/consolidate-adjacent
 * work) for each store, then write the step marker. A later consolidation change replaces the body behind this seam.
 */
export const prunePhase = async (session: AgentSession, deps: ClosePhaseDeps): Promise<void> => {
  if (isStepDone(session, CLOSE_STEPS.prune)) {
    deps.log.debug("prune step already done — skipping");
    return;
  }

  for (const store of MEMORY_STORES) {
    await runStoreMaintenance(store, deps);
  }

  markStepDone(session, CLOSE_STEPS.prune);
};

/**
 * Phase 3 — consolidation. The close pipeline owns the trigger, ordering, and the
 * idempotent step-marker seam; the cross-store consolidation BODY is owned by a later
 * memory-consolidation change. Interim, the phase is a marker-guarded no-op — running a real pass
 * here would just duplicate the per-store prune (phase 2) and the core-context update (next step),
 * so there is no honest interim work to do until that change supplies a distinct staged/atomic
 * consolidation.
 *
 * TODO: implement the consolidation body (per the design at the `main` tier) behind this seam.
 */
export const consolidatePhase = async (
  session: AgentSession,
  deps: ClosePhaseDeps,
): Promise<void> => {
  if (isStepDone(session, CLOSE_STEPS.consolidate)) {
    deps.log.debug("consolidate step already done — skipping");
    return;
  }

  markStepDone(session, CLOSE_STEPS.consolidate);
};

/**
 * Once-daily core-context update step (SOUL/USER/AGENTS) — the work the removed
 * `memory-context-maintenance` cron did. Marker-guarded so a re-run after a crash repeats it at most once.
 */
export const coreContextStep = async (
  session: AgentSession,
  deps: ClosePhaseDeps,
): Promise<void> => {
  if (isStepDone(session, CLOSE_STEPS.coreContext)) {
    deps.log.debug("core-context step already done — skipping");
    return;
  }

  await runContextMaintenance(deps);

  markStepDone(session, CLOSE_STEPS.coreContext);
};

const runStoreMaintenance = async (store: MemoryStore, deps: ClosePhaseDeps): Promise<void> => {
  deps.log.info({ store }, "close-pipeline store maintenance started");

  const start = Date.now();

  const result = await deps.side.run({
    tools: FILE_EDIT_TOOLS,
    system: await maintenanceSystemPrompt(store, deps),
    prompt: "Perform the maintenance pass now, following your instructions.",
    tier: "processor",
  });

  await sweepEmptyMarkdown(storeDir(deps.workspaceRoot, store), deps.log);

  await deps.commitChanges?.(`chore(memory): ${store} maintenance`);

  deps.log.info(
    { store, producedOutput: result.text.length > 0, durationMs: Date.now() - start },
    "close-pipeline store maintenance completed",
  );
};

const runContextMaintenance = async (deps: ClosePhaseDeps): Promise<void> => {
  deps.log.info("close-pipeline context maintenance started");

  const start = Date.now();

  const result = await deps.side.run({
    tools: FILE_EDIT_TOOLS,
    system: await contextMaintenanceSystemPrompt(deps.workspaceRoot),
    prompt: "Perform the context file cleanup pass now, following your instructions.",
    tier: "processor",
  });

  await deps.commitChanges?.("chore(memory): context file maintenance");

  deps.log.info(
    { producedOutput: result.text.length > 0, durationMs: Date.now() - start },
    "close-pipeline context maintenance completed",
  );
};

export interface TrunkClosePipelineDeps {
  extraction: CloseExtractionDeps;
  phases: ClosePhaseDeps;
}

/**
 * The trunk-close memory pipeline as a single `main`-phase post-processor. The phases run STRICTLY
 * ORDERED — extraction → prune → consolidate → core-context — inside one processor (the phased
 * post-processing runner parallelizes within a phase, so a single processor is what guarantees order).
 * Background runs (no trunk) are a no-op: those have no day's branches to fold.
 */
export const createTrunkClosePipeline = (deps: TrunkClosePipelineDeps): PostProcessor => ({
  name: "memory-trunk-close",
  phase: "main",

  async process({ trunk, log }) {
    if (trunk == null) {
      log.debug("no trunk — skipping trunk-close memory pipeline");
      return;
    }

    log.info(
      { day: trunk.day, branches: trunk.branchRecords.length },
      "trunk-close memory pipeline started",
    );

    const start = Date.now();

    const phases = { ...deps.phases, log };

    await extractBranches(trunk.session, trunk.branchRecords, deps.extraction, log);
    await prunePhase(trunk.session, phases);
    await consolidatePhase(trunk.session, phases);
    await coreContextStep(trunk.session, phases);

    log.info(
      { day: trunk.day, branches: trunk.branchRecords.length, durationMs: Date.now() - start },
      "trunk-close memory pipeline completed",
    );
  },
});
