import { mkdir } from "node:fs/promises";

import { type Static, Type } from "typebox";

import { NOTIFY_EVENT, SEVERITIES } from "../../events.ts";
import { hasRemote } from "../../git/git.ts";
import type { Logger } from "../../log.ts";
import { defineExtension, type PostProcessor } from "../api.ts";
import { analyzeBranches, type BranchForker, type Runner, runMaintenance } from "./analyze.ts";
import { ensureSkillEvolutionLayout, impactLogPath, skillEvolutionDir } from "./layout.ts";
import { ProposalRunError, type ReportedProposal, runProposalAgent } from "./propose.ts";
import { gitReconcileDeps, type ReconcileResult, reconcileProposals } from "./reconcile.ts";
import { reportRun } from "./report.ts";
import { filterEligible, listPatternPages, readImpactLog } from "./store.ts";
import { gitVerifyDeps, verifyAndRecord } from "./verify.ts";

// Flat config; enabled by default (R13), post-work prompt optional (R10).
export const SkillEvolutionConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  postWorkPrompt: Type.Optional(Type.String()),
});

export type SkillEvolutionConfig = Static<typeof SkillEvolutionConfigSchema>;

/**
 * Every stage the close run composes (S3, the design's Data Flow). Each seam defaults to its real
 * implementation; tests fake them structurally (DES-003 — no LLM, no git where a plain function
 * answers). `reconcile` narrows its input to what the processor owns — the default binds the git
 * dependency set, so a fake only has to answer with a result.
 */
export interface SkillEvolutionStages {
  /** The origin gate (R15): false skips the whole run before anything is touched. */
  hasRemote: (cwd: string, remote: string) => Promise<boolean>;
  reconcile: (input: { workspaceRoot: string; log: Logger }) => Promise<ReconcileResult>;
  analyze: typeof analyzeBranches;
  maintenance: typeof runMaintenance;
  proposal: typeof runProposalAgent;
  verify: typeof verifyAndRecord;
  report: typeof reportRun;
}

/** The production stage set — the real functions over the workspace checkout. */
const gitStages = (): SkillEvolutionStages => ({
  hasRemote,
  reconcile: ({ workspaceRoot, log }) =>
    reconcileProposals({ workspaceRoot, log, deps: gitReconcileDeps(workspaceRoot) }),
  analyze: analyzeBranches,
  maintenance: runMaintenance,
  proposal: runProposalAgent,
  verify: verifyAndRecord,
  report: reportRun,
});

export interface SkillEvolutionProcessorDeps {
  /** Cut a branch file, then fork its conversation — the analysis walk's forker. */
  agent: BranchForker;
  side: Runner;
  workspaceRoot: string;
  /** Feature-owned worktree namespace (`{workspace}/.tachikoma/tmp/skill-evolution`). */
  tmpDir: string;
  /** The app event bus emit — the dispatch and the fail-soft warning both ride it. */
  emit: (event: string, payload: unknown) => void;
  postWorkPrompt?: string;
  /** Injects the impact-log row dates; defaults to the wall clock. */
  now?: () => Date;
  /** Stage overrides — every seam defaults to its real implementation. */
  stages?: Partial<SkillEvolutionStages>;
}

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * The trunk-close run as one `main`-phase post-processor (S3): it runs concurrently with
 * `memory-trunk-close` through the phased runner's within-phase `allSettled`, sharing the same
 * trunk session and branch-records snapshot but writing a disjoint store.
 *
 * The whole body sits in ONE fail-soft wrapper (R11): any throw — including the re-thrown proposal
 * error from stage 10 — becomes a warn log plus a warning notification, and the processor still
 * resolves, so the close is never blocked. Durable partial progress is the walk's job: markers
 * land per branch as bodies complete, so a mid-run failure leaves completed branches analyzed
 * forever and abandons the rest with the retiring trunk.
 */
export const createSkillEvolutionProcessor = (
  deps: SkillEvolutionProcessorDeps,
): PostProcessor => ({
  name: "skill-evolution-trunk-close",
  phase: "main",
  statusLabel: "Evolving skills",

  async process({ trunk, log, status }) {
    const stages = { ...gitStages(), ...deps.stages };
    const { workspaceRoot, tmpDir, emit } = deps;
    const now = deps.now ?? (() => new Date());

    // The fail-soft boundary: warn + notify, never rethrow (R11 — memory keeps the trunk unclosed
    // on failure; this extension must not delay or block the close for a best-effort feature).
    try {
      if (trunk == null) {
        log.debug("no trunk — skipping skill-evolution run");
        return;
      }

      // The origin gate (R15): no remote to push proposals to means no run at all — logged, no
      // analysis forks, no run-time store writes. (Distinct from a reconcile abort, which warns.)
      if (!(await stages.hasRemote(workspaceRoot, "origin"))) {
        log.info("skill-evolution run skipped: no origin");
        return;
      }

      // Reconciliation (R1) always runs first: fresh remote state, then classify yesterday's
      // proposals. A soft abort (fetch or default-branch failure) skips the whole run — analyzing
      // and proposing against stale or unresolved refs is what the abort exists to prevent.
      status?.("Reconciling skill proposals…");
      const reconciled = await stages.reconcile({ workspaceRoot, log });

      if (reconciled.aborted) {
        log.warn(reconciled.reason);
        emit(NOTIFY_EVENT, {
          text: `Skill evolution skipped: ${reconciled.reason}`,
          severity: SEVERITIES.warning,
          source: "skill-evolution",
        });
        return;
      }

      log.info(
        { updated: reconciled.updated, classified: reconciled.classifications.length },
        "skill-evolution proposals reconciled",
      );

      // The analysis walk (R2/R3/R12): one conversation-aware fork per unanalyzed topic branch on
      // the shared walk. A failing branch is isolated and logged — the day continues (R11).
      const { failed } = await stages.analyze(
        trunk.session,
        trunk.sessionFile,
        trunk.branchRecords,
        trunk.day,
        { agent: deps.agent, workspaceRoot, log, status },
      );

      if (failed.length > 0) {
        log.warn(
          { failed: failed.map((record) => record.branchId) },
          "skill-evolution analysis failed for some branches — continuing with the day",
        );
      }

      await stages.maintenance({ side: deps.side, workspaceRoot, log });

      // Eligibility (R1/R8): patterns with ANY ledger entry never re-propose — enforced input-side,
      // so a re-run close cannot duplicate an open proposal. Empty → done, no LLM call.
      const impactLog = await readImpactLog(impactLogPath(workspaceRoot), log);
      const eligible = filterEligible(
        await listPatternPages(skillEvolutionDir(workspaceRoot)),
        impactLog,
        log,
      );

      if (eligible.length === 0) {
        log.info("no eligible patterns — proposal stage skipped");
        return;
      }

      // The proposal agent authors one branch per eligible pattern (R7–R9). A death here must NOT
      // skip verification: whatever pushed before the death is still verified, logged, swept, and
      // reported from git state (R8/R10 partial-failure guarantees) — capture, don't propagate.
      await mkdir(tmpDir, { recursive: true });

      let proposalError: unknown = null;
      let reported: ReportedProposal[] = [];

      try {
        reported = await stages.proposal({
          side: deps.side,
          workspaceRoot,
          tmpDir,
          defaultBranch: reconciled.defaultBranch,
          eligible,
          impactLog,
          log,
        });
      } catch (error) {
        proposalError = error;
        // A dying run still surfaces whatever `report_proposals` captured before the death —
        // those entries keep flowing into verification below.
        if (error instanceof ProposalRunError) reported = error.proposals;
        log.warn(
          { err: error },
          "skill-evolution proposal agent failed — verifying whatever pushed",
        );
      }

      // Verification ALWAYS runs (R8): host-only, from git state, with its own try/finally sweep
      // of the tmp worktrees and local proposal branches.
      status?.(`Verifying ${reported.length} proposals…`);
      const verified = await stages.verify({
        workspaceRoot,
        tmpDir,
        reported,
        defaultBranch: reconciled.defaultBranch,
        now,
        log,
        deps: gitVerifyDeps,
      });

      // The reporter fires only on at least one verified proposal (R10) — zero verified (a clean
      // decline, or every push denied) dispatches nothing.
      if (verified.length >= 1) {
        stages.report({ emit, workspaceRoot, verified, postWorkPrompt: deps.postWorkPrompt });
      }

      // Surface the captured proposal failure through the boundary wrapper, AFTER verification and
      // reporting settled: the run still fails soft (warn + notify), with everything durable kept.
      if (proposalError != null) {
        throw proposalError;
      }

      log.info({ verified: verified.length }, "skill-evolution run completed");
    } catch (error) {
      const text = `Skill evolution failed: ${messageOf(error)}`;
      log.warn({ err: error }, "skill-evolution run failed — trunk close continues (fail-soft)");
      emit(NOTIFY_EVENT, { text, severity: SEVERITIES.warning, source: "skill-evolution" });
    }
  },
});

/**
 * Self-healing skills (DLT-080): a trunk-close pass, running alongside memory post-processing,
 * that analyzes the day's topic branches for skill friction, accumulates the evidence as
 * pattern pages under `memories/skill-evolution/`, and turns recurring patterns into pushed,
 * reviewable proposal branches — never silent edits to the live skills.
 */
export default defineExtension<SkillEvolutionConfig>({
  name: "skill-evolution",

  configSchema: SkillEvolutionConfigSchema,

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("skill-evolution extension disabled by configuration");
      return;
    }

    // Processors don't receive the workspace — capture the root in closure (memory's idiom).
    const workspaceRoot = app.workspace.root;

    app.bootstrap("init-skill-evolution-layout", () =>
      ensureSkillEvolutionLayout(workspaceRoot, app.log),
    );

    app.sessions.registerProcessor(
      createSkillEvolutionProcessor({
        agent: {
          forkAndContinue: app.agent.forkAndContinue,
          branchFile: app.agent.branchFile,
        },
        side: app.agent.side,
        workspaceRoot,
        tmpDir: app.workspace.resolve(".tachikoma", "tmp", "skill-evolution"),
        // An arrow, not the method: `EventBus.emit` reads `this.handlers` (memory's emit idiom).
        emit: (event, payload) => app.events.emit(event, payload),
        postWorkPrompt: app.extensionConfig.postWorkPrompt,
      }),
    );
  },
});
