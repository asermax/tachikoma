import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { FILE_EDIT_TOOLS } from "../../agent/file-tools.ts";
import type { AgentManager } from "../../agent/manager.ts";
import type { Logger } from "../../log.ts";
import { walkBranches } from "../../sessions/branch-walk.ts";
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
  FORK_WRITE_STORES,
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
  /**
   * Optional progress surface: each phase emits a user-facing line as it starts (extraction emits
   * per branch too). Populated per invocation by `createTrunkClosePipeline` alongside the
   * per-invocation `log` override, since the coordinator's lifecycle routing is transient per
   * close. Optional-chained so a headless run with no callback emits nothing.
   */
  status?: (text: string) => void;
}

/**
 * Phase 1 — per-branch extraction, on the shared branch walk (`walkBranches`). For every branch
 * lacking an `extracted` marker, the walk forks ONLY that branch's conversation (root → its
 * original leaf, sliced conceptually from its base forward) and hands it to this phase's body: the
 * extraction forks (episodic + topics — learnings is folded into the topics fork, never its own)
 * plus their post-run sweeps. Idempotent: a re-run skips branches that already carry a marker. The
 * temp branch file is deleted after the body settles (the walk's settle-then-delete guarantee).
 *
 * A single branch's stores (episodic, topics) run concurrently by default — they write disjoint
 * directories, so there is no contention. The body awaits every store to settlement (all-settled)
 * before returning, so the walk's cleanup never deletes the branch file while a sibling fork is
 * still copying it. Set `parallelize: false` to fall back to one store at a time. Cross-branch
 * concurrency is intentionally NOT done here: same-store forks across branches share the canonical
 * store files, so that needs the separate fan-out/synthesis extraction mode (single synthesis
 * writer) rather than this flag.
 *
 * A single branch's failure is isolated — the walk logs it and skips the marker (so it retries on
 * the next close) rather than aborting the whole day. If ANY branch failed, this function throws
 * so the pipeline does not advance to the downstream phases and the trunk stays unclosed for a
 * retry (markers keep the already-extracted branches from being redone).
 */
export const extractBranches = async (
  session: AgentSession,
  records: BranchRecord[],
  { agent, workspaceRoot, parallelize = true }: CloseExtractionDeps,
  day: string,
  log: Logger,
  status?: (text: string) => void,
): Promise<void> => {
  const sessionFile = session.sessionFile;

  if (sessionFile == null) {
    log.warn("trunk session has no file — cannot fork branches for extraction");
    return;
  }

  // The per-branch body: each store's fork copies the walk's throwaway branchFile into its own
  // session (the source is read-only), hard-limited to file tools so it reuses the persona without
  // messaging or tasks.
  const body = async (record: BranchRecord, branchFile: string): Promise<void> => {
    const runStore = async (store: ExtractionStore): Promise<void> => {
      await agent.forkAndContinue(
        branchFile,
        branchStoreInstruction(store, workspaceRoot, record, day),
        "processor",
        FILE_EDIT_TOOLS,
      );

      // Sweep every directory this fork writes — the topics fork writes learnings/ too.
      for (const swept of FORK_WRITE_STORES[store]) {
        await sweepEmptyMarkdown(storeDir(workspaceRoot, swept), log);
      }
    };

    // allSettled (not Promise.all) so the walk never deletes branchFile while a sibling fork is
    // still copying it — a first-rejection short-circuit could tear the shared file down mid-read.
    if (parallelize) {
      const outcomes = await Promise.allSettled(EXTRACTION_STORES.map(runStore));
      const rejection = outcomes.find(
        (outcome): outcome is PromiseRejectedResult => outcome.status === "rejected",
      );
      if (rejection) throw rejection.reason;
    } else {
      for (const store of EXTRACTION_STORES) await runStore(store);
    }
  };

  const { failed } = await walkBranches(session, sessionFile, records, day, {
    agent,
    body,
    isDone: (trunkSession, record) => isBranchExtracted(trunkSession, record.branchId),
    markDone: (trunkSession, record) => markBranchExtracted(trunkSession, record.branchId),
    log,
    status,
    progress: {
      start: (i, n) => `Extracting branch ${i}/${n}…`,
      done: (i, n) => `Extracted branch ${i}/${n}`,
      failed: (i, n) => `Branch ${i}/${n} failed — will retry`,
    },
  });

  if (failed.length > 0) {
    throw new Error(
      `branch extraction failed for ${failed.length} branch(es): ${failed
        .map((record) => record.branchId)
        .join(", ")}`,
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
    deps.status?.(`Pruning ${store} memories…`);
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

  // Surface the phase boundary even while the body is an interim no-op, so the close narrative
  // stays complete (and ready for the consolidation body DLT-173 will slot in here).
  deps.status?.("Consolidating memories…");

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

  deps.status?.("Updating core context…");

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

  async process({ trunk, log, status }) {
    if (trunk == null) {
      log.debug("no trunk — skipping trunk-close memory pipeline");
      return;
    }

    const branches = trunk.branchRecords.length;

    // Open with the day + branch count. The generic statusLabel fired by the coordinator just
    // preceded this, so this specific opener immediately supersedes it.
    status?.(
      `Closing ${trunk.day}${branches > 0 ? ` — ${branches} branch${branches > 1 ? "es" : ""}` : ""}`,
    );

    log.info({ day: trunk.day, branches }, "trunk-close memory pipeline started");

    const start = Date.now();

    // `status` is per-invocation (the coordinator's lifecycle routing is transient per close), so it
    // rides alongside the per-invocation `log` override into the phase deps.
    const phases = { ...deps.phases, log, status };

    await extractBranches(
      trunk.session,
      trunk.branchRecords,
      deps.extraction,
      trunk.day,
      log,
      status,
    );
    await prunePhase(trunk.session, phases);
    await consolidatePhase(trunk.session, phases);
    await coreContextStep(trunk.session, phases);

    log.info(
      { day: trunk.day, branches, durationMs: Date.now() - start },
      "trunk-close memory pipeline completed",
    );
  },
});
