import { type Static, Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { defineExtension } from "../api.ts";
import { createTranscriptArchiveProcessor, pruneTranscripts } from "./archive.ts";
import { createTrunkClosePipeline } from "./close-pipeline.ts";
import { buildMemoryContext } from "./indexes.ts";
import { ensureMemoryLayout } from "./layout.ts";

export const MemoryConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  maintenance: Type.Object(
    {
      enabled: Type.Boolean({ default: true }),
      recentDays: Type.Number({ default: 15 }),
      weeklyThresholdMonths: Type.Number({ default: 3 }),
      monthlyThresholdMonths: Type.Number({ default: 12 }),
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
 * (episodic, facts, preferences, transcripts). Injects a static index of the
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

    app.bootstrap("init-memory-layout", () => ensureMemoryLayout(workspaceRoot, app.log));

    app.agent.use(
      provideContext(() => buildMemoryContext(workspaceRoot, app.log), "memories"),
      { sessionScopes: ["main", "background"] },
    );

    if (maintenance.enabled) {
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

      // The whole nightly memory pipeline now runs at trunk close: per-branch extraction,
      // prune, consolidation, and the once-daily core-context update — each guarded by an idempotent
      // marker on the session file. The phase bodies are a pluggable seam (close-pipeline.ts) reusing
      // the existing maintenance logic; a later consolidation change replaces them behind the same functions.
      app.sessions.registerProcessor(
        createTrunkClosePipeline({
          extraction: { agent: { forkAndContinue: app.agent.forkAndContinue }, workspaceRoot },
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
