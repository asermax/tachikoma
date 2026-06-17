import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { Logger } from "../../log.ts";

/**
 * Mid-session resource reload, pi-style: a /reload command (reload must run in
 * command context) plus a tool that queues it, so the agent can refresh skills
 * itself when the user mentions adding or editing one. New sessions always
 * rediscover skills regardless — this covers the live session.
 */
export const registerReload = (pi: ExtensionAPI, log: Logger): void => {
  pi.registerCommand("reload", {
    description: "Reload skills, prompts, and extensions from disk",
    handler: async (_args, ctx) => {
      log.info("reloading skills/prompts/extensions from disk");

      const start = Date.now();
      try {
        await ctx.reload();
      } catch (error) {
        log.warn({ err: error, durationMs: Date.now() - start }, "resource reload failed");

        throw error;
      }

      log.info({ durationMs: Date.now() - start }, "resource reload completed");
    },
  });

  pi.registerTool({
    name: "reload_resources",
    label: "Reload Resources",
    description:
      "Reload skills, prompts, and extensions from disk so additions and edits become available in the current session.",
    parameters: Type.Object({}),
    promptSnippet: "Reload skills/prompts/extensions from disk mid-session",
    promptGuidelines: [
      "Use reload_resources when the user says they added or changed a skill, so it loads without restarting.",
    ],
    async execute() {
      pi.sendUserMessage("/reload", { deliverAs: "followUp" });

      return {
        content: [
          {
            type: "text",
            text: "Queued /reload — resources refresh when the current run finishes.",
          },
        ],
        details: {},
      };
    },
  });
};
