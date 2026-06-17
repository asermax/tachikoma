import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { discoverSkillAgents } from "./agents.ts";
import { BUILTIN_AGENTS } from "./builtins.ts";
import { createDelegateTool } from "./delegate.ts";
import { registerReload } from "./reload.ts";
import { registerSkillSuggestion } from "./suggest.ts";

interface SkillsConfig {
  enabled: boolean;
  proactiveLoading: boolean;
}

// Built-in authoring skills ship alongside this extension, in its builtin-skills/ directory.
const builtinSkillsDir = resolve(import.meta.dirname, "builtin-skills");

/**
 * Workspace skills: contributes the workspace skills directory as a pi skill source.
 * pi natively handles discovery, progressive disclosure, and /skill commands, so the
 * extension only wires the source and exposes skill-bundled agent definitions through
 * a delegate_to_agent tool.
 */
export default defineExtension<SkillsConfig>({
  name: "skills",

  configSchema: Type.Object({
    enabled: Type.Boolean({ default: true }),
    // Per-turn conversation-aware classifier that proactively loads relevant skills (augments
    // pi's progressive disclosure). Disable to fall back to pi-native loading only.
    proactiveLoading: Type.Boolean({ default: true }),
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.debug("skills disabled by configuration");
      return;
    }

    const skillsDir = app.workspace.resolve("skills");

    app.bootstrap("ensure-skills-dir", async () => {
      await mkdir(skillsDir, { recursive: true });
    });

    app.agent.use(
      (pi) => {
        pi.on("resources_discover", () => ({ skillPaths: [skillsDir, builtinSkillsDir] }));

        registerReload(pi, app.log);

        if (app.extensionConfig.proactiveLoading) {
          registerSkillSuggestion(pi, {
            classifier: app.agent.side,
            isForking: app.agent.isForking,
            status: app.status,
            log: app.log,
          });
        }

        // Discovery runs per agent session, so new skill agents appear on the next session without
        // a restart. The built-in general-purpose agent is always present, so delegation is always
        // available — skill agents simply extend the roster.
        const discover = () => [...BUILTIN_AGENTS, ...discoverSkillAgents(skillsDir, app.log)];

        pi.registerTool(createDelegateTool({ discover, runner: app.agent.side, log: app.log }));
      },
      { sessionScopes: ["main", "background"] },
    );
  },
});
