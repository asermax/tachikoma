import { type Static, Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { defineExtension } from "../api.ts";
import { createCoreContextProcessor } from "../context/processor.ts";
import { commitAll } from "../git/commit.ts";
import { createTranscriptArchiveProcessor, pruneTranscripts } from "./archive.ts";
import { createExtractionProcessor } from "./extraction.ts";
import { buildMemoryContext } from "./indexes.ts";
import { ensureMemoryLayout, MEMORY_STORES } from "./layout.ts";
import { runContextMaintenanceTick, runMaintenanceTick } from "./maintenance.ts";

export const MemoryConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  maintenance: Type.Object(
    {
      enabled: Type.Boolean({ default: true }),
      // Staggered so the three headless agent runs don't pile up at the same minute.
      episodicSchedule: Type.String({ default: "0 3 * * *" }),
      factsSchedule: Type.String({ default: "20 3 * * *" }),
      preferencesSchedule: Type.String({ default: "40 3 * * *" }),
      // Foundational context (SOUL/USER/AGENTS) cleanup; staggered after the store ticks.
      contextSchedule: Type.String({ default: "0 4 * * *" }),
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
 * store on every message, extracts memories at session close, archives the pi
 * transcript, and consolidates the store on a nightly schedule.
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

    app.bootstrap("init-memory-layout", () => ensureMemoryLayout(workspaceRoot, app.log));

    app.agent.use(
      provideContext(() => buildMemoryContext(workspaceRoot), "memories"),
      { sessionScopes: ["main", "background"] },
    );

    // Each store registers its own processor; phase:"main" runs them via Promise.allSettled,
    // so the three run as three parallel forks of the just-ended conversation.
    const extraction = { agent: app.agent, workspaceRoot };

    for (const store of MEMORY_STORES) {
      app.sessions.registerProcessor(createExtractionProcessor(store, extraction));
    }

    // Registered here for now — once the context extension grows its own
    // processor wiring this registration belongs in context/index.ts.
    app.sessions.registerProcessor(
      createCoreContextProcessor({
        agent: app.agent,
        workspaceRoot,
        dataDir: app.workspace.dataDir,
      }),
    );

    app.sessions.registerProcessor(createTranscriptArchiveProcessor(workspaceRoot));

    const { maintenance } = app.extensionConfig;

    if (maintenance.enabled) {
      const commitChanges = async (message: string): Promise<void> => {
        try {
          const committed = await commitAll({
            cwd: workspaceRoot,
            message,
            fallbackMessage: message,
            log: app.log,
          });

          if (committed != null) app.log.info({ message }, "committed memory maintenance changes");
        } catch (error) {
          app.log.warn({ err: error }, "memory maintenance commit failed");
        }
      };

      const deps = {
        side: app.agent.side,
        workspaceRoot,
        settings: maintenance,
        log: app.log,
        commitChanges,
      };

      app.scheduler.cron("memory-episodic-maintenance", maintenance.episodicSchedule, () =>
        runMaintenanceTick("episodic", deps),
      );
      app.scheduler.cron("memory-facts-maintenance", maintenance.factsSchedule, () =>
        runMaintenanceTick("facts", deps),
      );
      app.scheduler.cron("memory-preferences-maintenance", maintenance.preferencesSchedule, () =>
        runMaintenanceTick("preferences", deps),
      );

      app.scheduler.cron("memory-context-maintenance", maintenance.contextSchedule, () =>
        runContextMaintenanceTick(deps),
      );

      app.scheduler.cron("memory-transcripts-maintenance", maintenance.transcriptsSchedule, () =>
        pruneTranscripts(workspaceRoot, maintenance.transcriptRetentionDays, app.log),
      );
    }
  },
});
