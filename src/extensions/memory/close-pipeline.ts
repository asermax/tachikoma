import { rm } from "node:fs/promises";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { AgentManager } from "../../agent/manager.ts";
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
import {
  EXTRACTION_STORES,
  type ExtractionStore,
  MEMORY_STORES,
  type MemoryStore,
  storeDir,
  sweepEmptyMarkdown,
} from "./layout.ts";
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

/** The slice of AgentManager per-branch extraction needs: cut a branch file, then fork its conversation. */
export type BranchForker = Pick<AgentManager, "forkAndContinue" | "branchFile">;

export interface CloseExtractionDeps {
  agent: BranchForker;
  workspaceRoot: string;
  /**
   * Run a branch's store extractions (episodic, topics) concurrently — they write disjoint
   * directories, so parallel is safe (default). Set false to extract one store at a time.
   */
  parallelize?: boolean;
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
 * the extraction forks over it (episodic + topics — learnings is folded into the topics fork, never
 * its own), then write the per-branch marker. Idempotent: a re-run skips
 * branches that already carry a marker. The temp branch file is deleted after the fork.
 *
 * Branches are extracted one at a time, but a single branch's stores (episodic, topics) run
 * concurrently by default — they write disjoint directories, so there is no contention. Each store's
 * fork is awaited to settlement (all-settled) before the shared branch file is deleted, so a sibling
 * fork is never left reading a file the `finally` is tearing down. Set `parallelize: false` to fall
 * back to one store at a time. Cross-branch concurrency is intentionally NOT done here: same-store
 * forks across branches share the canonical store files, so that needs the separate fan-out/synthesis
 * extraction mode (single synthesis writer) rather than this flag.
 *
 * A single branch's failure is isolated — it is logged and skipped (no marker, so it retries on the
 * next close) rather than aborting the whole day. If ANY branch failed, the function throws after the
 * loop so the pipeline does not advance to the downstream phases and the trunk stays unclosed for a
 * retry (markers keep the already-extracted branches from being redone).
 */
export const extractBranches = async (
  session: AgentSession,
  records: BranchRecord[],
  { agent, workspaceRoot, parallelize = true }: CloseExtractionDeps,
  day: string,
  log: Logger,
): Promise<void> => {
  const sessionFile = session.sessionFile;

  if (sessionFile == null) {
    log.warn("trunk session has no file — cannot fork branches for extraction");
    return;
  }

  const failed: string[] = [];

  for (const record of records) {
    if (isBranchExtracted(session, record.branchId)) {
      log.debug({ branchId: record.branchId }, "branch already extracted — skipping");
      continue;
    }

    // Cut the branch from a manager loaded fresh off disk — NOT the live trunk session — so this
    // never mutates the session whose other branches we still need to walk (see AgentManager.branchFile).
    const branchFile = agent.branchFile(sessionFile, record.originalLeafId);

    if (branchFile == null) {
      log.warn({ branchId: record.branchId }, "could not fork branch for extraction — skipping");
      continue;
    }

    const start = Date.now();

    // Each store's fork copies this throwaway branchFile into its own session (the source is
    // read-only), hard-limited to file tools so it reuses the persona without messaging or tasks.
    const runStore = async (store: ExtractionStore): Promise<void> => {
      await agent.forkAndContinue(
        branchFile,
        branchStoreInstruction(store, workspaceRoot, record, day),
        "processor",
        FILE_EDIT_TOOLS,
      );

      await sweepEmptyMarkdown(storeDir(workspaceRoot, store), log);
    };

    try {
      try {
        // allSettled (not Promise.all) so the `finally` never deletes branchFile while a sibling fork
        // is still copying it — a first-rejection short-circuit could tear the shared file down mid-read.
        if (parallelize) {
          const outcomes = await Promise.allSettled(EXTRACTION_STORES.map(runStore));
          const rejection = outcomes.find(
            (outcome): outcome is PromiseRejectedResult => outcome.status === "rejected",
          );
          if (rejection) throw rejection.reason;
        } else {
          for (const store of EXTRACTION_STORES) await runStore(store);
        }
      } finally {
        await rm(branchFile, { force: true });
      }

      markBranchExtracted(session, record.branchId);

      log.info({ branchId: record.branchId, durationMs: Date.now() - start }, "branch extracted");
    } catch (error) {
      failed.push(record.branchId);
      log.error({ err: error, branchId: record.branchId }, "branch extraction failed — skipping");
    }
  }

  if (failed.length > 0) {
    throw new Error(
      `branch extraction failed for ${failed.length} branch(es): ${failed.join(", ")}`,
    );
  }
};

/** The follow-up instruction for one store, noting the fork is a single topic branch's own turns. */
const branchStoreInstruction = (
  store: ExtractionStore,
  workspaceRoot: string,
  record: BranchRecord,
  day: string,
): string =>
  `${storeInstruction(store, workspaceRoot, day)}\n\nThis conversation is a single topic branch (\`${record.branchId}\`) from the ${day} session. Focus only on this branch's own turns.`;

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
  statusLabel: "Processing memories",

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

    await extractBranches(trunk.session, trunk.branchRecords, deps.extraction, trunk.day, log);
    await prunePhase(trunk.session, phases);
    await consolidatePhase(trunk.session, phases);
    await coreContextStep(trunk.session, phases);

    log.info(
      { day: trunk.day, branches: trunk.branchRecords.length, durationMs: Date.now() - start },
      "trunk-close memory pipeline completed",
    );
  },
});
