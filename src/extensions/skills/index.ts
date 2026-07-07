import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { Type } from "typebox";

import { provideContext } from "../../agent/system-prompt-section.ts";
import { SESSION_TOPIC_CHANGED_EVENT } from "../../events.ts";
import { defineExtension, SESSION_SCOPES } from "../api.ts";
import { discoverSkillAgents } from "./agents.ts";
import { BUILTIN_AGENTS } from "./builtins.ts";
import { createDelegateTool } from "./delegate.ts";
import { registerReload } from "./reload.ts";
import { registerSkillSuggestion } from "./suggest.ts";
import { SKILLS_USAGE } from "./usage.ts";

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
      (pi, session) => {
        pi.on("resources_discover", () => ({ skillPaths: [skillsDir, builtinSkillsDir] }));

        registerReload(pi, app.log);

        if (app.extensionConfig.proactiveLoading) {
          registerSkillSuggestion(pi, {
            classifier: app.agent.side,
            isForking: app.agent.isForking,
            // A background task session has no user-facing streaming surface, so a "Checking for relevant
            // skills…" status would orphan as a stray lead-in no main renderer reclaims. Keep the proactive
            // classifier running (its injected skill content is hidden, display:false) but give it no status
            // surface there — main sessions still surface the line through the channel.
            status: session.scope === SESSION_SCOPES.main ? app.status : () => {},
            // Reset per-branch injection state on a genuine topic shift so the new branch re-evaluates
            // skills from scratch. Main scope only — background task sessions have no topic shifts, and
            // the main-session factory runs once per process (the single subscription lives for the trunk).
            onTopicChanged:
              session.scope === SESSION_SCOPES.main
                ? (handler) => app.events.on(SESSION_TOPIC_CHANGED_EVENT, handler)
                : undefined,
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

    // Agent-facing skill guidance (catalog habit + injected-skill authority), contributed as the
    // extension's own usage section so the core base prompt stays feature-agnostic. Not gated by
    // proactiveLoading: the catalog/progressive-disclosure guidance is relevant even when proactive
    // injection is off. Scoped like the factory above so background task runs receive it too.
    app.agent.use(provideContext(SKILLS_USAGE, "skills-usage"), {
      sessionScopes: ["main", "background"],
    });
  },
});
