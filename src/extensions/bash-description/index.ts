/**
 * Bash tool override that adds a required `description` parameter.
 *
 * Forces the LLM to explain what each command does before running it, so the
 * Telegram formatter can surface that explanation to the user in live labels
 * and baked activity markers. The original bash tool handles execution and
 * rendering unchanged.
 */
import { type ExtensionFactory, createBashToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import type { TachikomaExtension } from "../api.ts";
import { SESSION_SCOPES } from "../api.ts";

const originalBash = createBashToolDefinition(process.cwd());

const extendedSchema = Type.Object({
  description: Type.String({
    description: "Brief explanation of what this command does and why",
  }),
  command: Type.String({ description: "Bash command to execute" }),
  timeout: Type.Optional(
    Type.Number({ description: "Timeout in seconds (optional, no default timeout)" }),
  ),
});

const factory: ExtensionFactory = (pi) => {
  pi.registerTool({
    ...originalBash,
    parameters: extendedSchema,
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      const { description: _, ...bashParams } = params;
      return originalBash.execute(toolCallId, bashParams, signal, onUpdate, ctx);
    },
  });
};

export default {
  name: "bash-description",
  setup(app) {
    app.agent.use(factory, { sessionScopes: [SESSION_SCOPES.main, SESSION_SCOPES.background] });
  },
} satisfies TachikomaExtension<never>;
