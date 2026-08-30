import { type Static, Type } from "typebox";

import { defineExtension } from "../api.ts";
import { ensureSkillEvolutionLayout } from "./layout.ts";

// Flat config; enabled by default (R13), post-work prompt optional (R10).
export const SkillEvolutionConfigSchema = Type.Object({
  enabled: Type.Boolean({ default: true }),
  postWorkPrompt: Type.Optional(Type.String()),
});

export type SkillEvolutionConfig = Static<typeof SkillEvolutionConfigSchema>;

/**
 * Self-healing skills (DLT-080): a trunk-close pass, running alongside memory post-processing,
 * that analyzes the day's topic branches for skill friction, accumulates the evidence as
 * pattern pages under `memories/skill-evolution/`, and turns recurring patterns into pushed,
 * reviewable proposal branches — never silent edits to the live skills. This skeleton owns the
 * config and the store layout bootstrap; the close processor lands with Batch 7.
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

    // Processor registration lands in Batch 7.
  },
});
