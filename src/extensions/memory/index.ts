import { type Static, Type } from "typebox";

import { defineExtension } from "../api.ts";
import { createCoreContextProcessor } from "../context/processor.ts";
import { createTranscriptArchiveProcessor } from "./archive.ts";
import { createExtractionProcessor } from "./extraction.ts";
import { createMemoryIndexProvider } from "./indexes.ts";
import { ensureMemoryLayout, MEMORY_STORES } from "./layout.ts";
import { runMaintenanceTick } from "./maintenance.ts";

const MemoryConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  /** Cap on the rendered conversation injected into extraction prompts (tail-priority). */
  maxTranscriptChars: Type.Number({ default: 24000 }),
  maintenance: Type.Object(
    {
      enabled: Type.Boolean({ default: true }),
      // Staggered so the three headless agent runs don't pile up at the same minute.
      episodicSchedule: Type.String({ default: "0 3 * * *" }),
      factsSchedule: Type.String({ default: "20 3 * * *" }),
      preferencesSchedule: Type.String({ default: "40 3 * * *" }),
      recentDays: Type.Number({ default: 15 }),
      weeklyThresholdMonths: Type.Number({ default: 3 }),
      monthlyThresholdMonths: Type.Number({ default: 12 }),
    },
    { default: {} },
  ),
});

type MemoryConfig = Static<typeof MemoryConfigSchema>;

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

    app.agent.provideContext(createMemoryIndexProvider(workspaceRoot));

    const extraction = {
      side: app.agent.side,
      workspaceRoot,
      maxTranscriptChars: app.extensionConfig.maxTranscriptChars,
    };

    for (const store of MEMORY_STORES) {
      app.sessions.registerProcessor(createExtractionProcessor(store, extraction));
    }

    // Registered here for now — once the context extension grows its own
    // processor wiring this registration belongs in context/index.ts.
    app.sessions.registerProcessor(
      createCoreContextProcessor({
        side: app.agent.side,
        workspaceRoot,
        dataDir: app.workspace.dataDir,
        maxTranscriptChars: app.extensionConfig.maxTranscriptChars,
      }),
    );

    app.sessions.registerProcessor(createTranscriptArchiveProcessor(workspaceRoot));

    const { maintenance } = app.extensionConfig;

    if (maintenance.enabled) {
      const deps = { side: app.agent.side, workspaceRoot, settings: maintenance, log: app.log };

      app.scheduler.cron("memory-episodic-maintenance", maintenance.episodicSchedule, () =>
        runMaintenanceTick("episodic", deps),
      );
      app.scheduler.cron("memory-facts-maintenance", maintenance.factsSchedule, () =>
        runMaintenanceTick("facts", deps),
      );
      app.scheduler.cron("memory-preferences-maintenance", maintenance.preferencesSchedule, () =>
        runMaintenanceTick("preferences", deps),
      );
    }
  },
});
