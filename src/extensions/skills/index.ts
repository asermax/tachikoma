import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { discoverSkillAgents } from "./agents.ts";
import { createDelegateTool } from "./delegate.ts";
import { registerReload } from "./reload.ts";

interface SkillsConfig {
  enabled: boolean;
}

// Built-in authoring skills ship inside the repo's skills/ directory, three levels
// up from this module (src/extensions/skills → repo root).
const builtinSkillsDir = resolve(import.meta.dirname, "../../../skills");

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
  }),

  setup(app) {
    if (!app.extensionConfig.enabled) {
      app.log.info("skills disabled by configuration");
      return;
    }

    const skillsDir = app.workspace.resolve("skills");

    app.bootstrap("ensure-skills-dir", async () => {
      await mkdir(skillsDir, { recursive: true });
    });

    app.agent.use((pi) => {
      pi.on("resources_discover", () => ({ skillPaths: [skillsDir, builtinSkillsDir] }));

      registerReload(pi);

      // Discovery runs per agent session, so new skill agents appear on the next
      // session without a restart. No tool is advertised when no agents exist.
      const discover = () => discoverSkillAgents(skillsDir, app.log);

      if (discover().length > 0) {
        pi.registerTool(createDelegateTool({ discover, runner: app.agent.side, log: app.log }));
      }
    });
  },
});
