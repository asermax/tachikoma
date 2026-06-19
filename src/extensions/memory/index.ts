import { type Static, Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { defineExtension } from "../api.ts";
import { createTranscriptArchiveProcessor, pruneTranscripts } from "./archive.ts";
import { createTrunkClosePipeline } from "./close-pipeline.ts";
import { buildMemoryContext } from "./indexes.ts";
import { ensureMemoryLayout } from "./layout.ts";
import { migrateMemoryStores } from "./migration.ts";

export const MemoryConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  maintenance: Type.Object(
    {
      enabled: Type.Boolean({ default: true }),
      recentDays: Type.Number({ default: 15 }),
      weeklyThresholdMonths: Type.Number({ default: 3 }),
      monthlyThresholdMonths: Type.Number({ default: 12 }),
      // Extract each branch's stores (episodic, topics) concurrently — they write disjoint dirs, so
      // parallel is safe. Set false to extract one store at a time.
      parallelizeExtraction: Type.Boolean({ default: true }),
      // Transcript archives are pruned by age (deterministic, no agent run); 0 keeps them forever.
      transcriptsSchedule: Type.String({ default: "50 3 * * *" }),
      transcriptRetentionDays: Type.Number({ default: 90 }),
    },
    { default: {} },
  ),
});

export type MemoryConfig = Static<typeof MemoryConfigSchema>;

/**
 * Long-term memory: a git-versioned markdown store under workspace `memories/`
 * (episodic, topics, learnings, transcripts). Injects a static index of the
 * store on every message; folds extraction + pruning + consolidation + the
 * once-daily core-context update into the trunk-close pipeline, and archives
 * the trunk transcript at close.
 */
export default defineExtension<MemoryConfig>({
  name: "memory",

  configSchema: MemoryConfigSchema,

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("memory extension disabled by configuration");
      return;
    }

    const workspaceRoot = app.workspace.root;
    const { maintenance } = app.extensionConfig;

    // Shared commit helper for every workspace-mutating memory pass (the migration fold/sweep and,
    // when maintenance is enabled, the trunk-close pipeline). Built from the workspace commit agent
    // + grouped commitAll so every edit lands as a git commit — which is also the migration's
    // backup/recovery mechanism. Defined at setup scope so the migration hook (which runs even when
    // maintenance is disabled) can reach it.
    const commitAgent = app.git.createCommitAgent("workspace");

    const commitChanges = async (message: string): Promise<void> => {
      try {
        const committed = await app.git.commitAll({
          agent: commitAgent,
          cwd: workspaceRoot,
          fallbackMessage: message,
        });

        if (committed.length > 0) app.log.info({ message }, "committed memory pipeline changes");
        else app.log.debug({ message }, "memory pipeline produced nothing to commit");
      } catch (error) {
        app.log.warn({ err: error }, "memory pipeline commit failed");
      }
    };

    // One-time fold of the legacy facts/ + preferences/ stores into topics/. Registered BEFORE
    // init-memory-layout (bootstrap hooks run in registration order — see host.ts) so the fold
    // creates topic files before the layout hook seeds/preserves their index, and ungated by
    // maintenance since it must run even when maintenance is disabled. Self-detecting + idempotent:
    // a no-op on fresh installs and already-migrated workspaces (empty legacy stores is the done
    // signal — no persisted marker).
    app.bootstrap("migrate-memory-stores", () =>
      migrateMemoryStores({ side: app.agent.side, workspaceRoot, log: app.log, commitChanges }),
    );

    app.bootstrap("init-memory-layout", () => ensureMemoryLayout(workspaceRoot, app.log));

    app.agent.use(
      provideContext(() => buildMemoryContext(workspaceRoot, app.log), "memories"),
      { sessionScopes: ["main", "background"] },
    );

    if (maintenance.enabled) {
      // The whole nightly memory pipeline now runs at trunk close: per-branch extraction,
      // prune, consolidation, and the once-daily core-context update — each guarded by an idempotent
      // marker on the session file. The phase bodies are a pluggable seam (close-pipeline.ts) reusing
      // the existing maintenance logic; a later consolidation change replaces them behind the same functions.
      app.sessions.registerProcessor(
        createTrunkClosePipeline({
          extraction: {
            agent: { forkAndContinue: app.agent.forkAndContinue, branchFile: app.agent.branchFile },
            workspaceRoot,
            parallelize: maintenance.parallelizeExtraction,
          },
          phases: {
            side: app.agent.side,
            workspaceRoot,
            settings: maintenance,
            log: app.log,
            commitChanges,
          },
        }),
      );
    }

    app.sessions.registerProcessor(createTranscriptArchiveProcessor(workspaceRoot));

    // Transcript retention stays a deterministic, host-side cleanup cron (no agent run) — it prunes
    // archived transcripts purely by age, unlike the agent-driven maintenance that moved into close.
    app.scheduler.cron("memory-transcripts-prune", maintenance.transcriptsSchedule, () =>
      pruneTranscripts(workspaceRoot, maintenance.transcriptRetentionDays, app.log),
    );
  },
});
