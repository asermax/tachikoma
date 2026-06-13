import { completeSimple, type Message } from "@earendil-works/pi-ai";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import type { Static, TSchema } from "typebox";

import { parseWithSchema } from "../config/parse.ts";
import type { Logger } from "../log.ts";
import type { AgentManager } from "./manager.ts";
import type { ModelTier } from "./models.ts";

export interface ClassifyOptions<S extends TSchema> {
  system: string;
  user: string;
  schema: S;
  tier?: ModelTier;
}

export interface CompleteOptions {
  system?: string;
  user: string;
  tier?: ModelTier;
}

export interface HeadlessRunOptions {
  prompt: string;
  system?: string;
  /** pi built-in tool names the run may use (e.g. ["read", "grep"]). Default: none. */
  tools?: string[];
  /** Extra in-process tools for this run (their names are enabled automatically). */
  customTools?: ToolDefinition[];
  tier?: ModelTier;
  /** Explicit "provider/model-id[:thinkingLevel]" reference; overrides `tier` when set. */
  model?: string;
  /** Isolate the system prompt — suppress pi's append/context-files/skills so it is exactly `system`. */
  isolatePrompt?: boolean;
  /** Bind the registered background factories (tools + skill sources) into this run. */
  backgroundExtensions?: boolean;
}

export interface HeadlessRunResult {
  text: string;
}

const textOf = (message: { content: { type: string }[] }): string =>
  message.content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("");

const extractJson = (text: string): string => {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fenced?.[1] != null) return fenced[1].trim();

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start >= 0 && end > start) return text.slice(start, end + 1);

  return text.trim();
};

/** Side-channel LLM work outside the conversational session: classification and one-shot completions. */
export class SideRunner {
  private readonly manager: AgentManager;
  private readonly log: Logger;

  constructor(manager: AgentManager, log: Logger) {
    this.manager = manager;
    this.log = log;
  }

  async complete({ system, user, tier = "processor" }: CompleteOptions): Promise<string> {
    const { model, fromPiDefaults } = this.manager.tiers.resolve(tier);

    if (fromPiDefaults) {
      this.log.debug(
        { tier, model: `${model.provider}/${model.id}` },
        "tier unset — using pi default model",
      );
    }

    const apiKey = await this.manager.apiKeyFor(model.provider);

    const messages: Message[] = [
      { role: "user", content: [{ type: "text", text: user }], timestamp: Date.now() },
    ];

    const result = await completeSimple(
      model,
      { ...(system != null ? { systemPrompt: system } : {}), messages },
      apiKey != null ? { apiKey } : undefined,
    );

    if (result.stopReason === "error" || result.stopReason === "aborted") {
      throw new Error(`side completion failed: ${result.errorMessage ?? result.stopReason}`);
    }

    return textOf(result);
  }

  /**
   * Headless agent run in an ephemeral pi session: tool use allowed, nothing persisted,
   * no Tachikoma extensions bound. Returns the final assistant text.
   */
  async run({
    prompt,
    system,
    tools = [],
    customTools,
    tier = "processor",
    model,
    isolatePrompt,
    backgroundExtensions,
  }: HeadlessRunOptions): Promise<HeadlessRunResult> {
    const session = await this.manager.open({
      inMemory: true,
      bare: true,
      tier,
      ...(model != null ? { model } : {}),
      ...(isolatePrompt === true ? { isolatePrompt: true } : {}),
      ...(backgroundExtensions === true ? { bindBackgroundFactories: true } : {}),
      // A hard tool allowlist (`options.tools`) also filters out extension/custom tools, so a
      // background run that binds factories must NOT set one — it runs with the main session's
      // tool model (default built-ins + all bound factory and custom tools active). Other runs
      // keep an explicit built-in allowlist (plus their custom tools' names).
      ...(backgroundExtensions === true
        ? {}
        : { tools: [...tools, ...(customTools ?? []).map((tool) => tool.name)] }),
      ...(customTools != null ? { customTools } : {}),
      ...(system != null ? { systemPrompt: system } : {}),
    });

    try {
      await session.prompt(prompt);

      for (let index = session.messages.length - 1; index >= 0; index -= 1) {
        const message = session.messages[index];
        if (message == null || message.role !== "assistant") continue;

        return { text: textOf(message as { content: { type: string }[] }) };
      }

      return { text: "" };
    } finally {
      session.dispose();
    }
  }

  /** Structured classification: instructs JSON output matching the schema, parses, retries once. */
  async classify<S extends TSchema>({
    system,
    user,
    schema,
    tier = "classifier",
  }: ClassifyOptions<S>): Promise<Static<S>> {
    const instruction = `${system}\n\nRespond with a single JSON object matching this JSON Schema — no prose:\n${JSON.stringify(schema)}`;

    const attempt = async (extra: string): Promise<Static<S>> => {
      const text = await this.complete({ system: instruction, user: `${user}${extra}`, tier });
      return parseWithSchema(schema, JSON.parse(extractJson(text)), "classification output");
    };

    try {
      return await attempt("");
    } catch (error) {
      this.log.debug({ err: error }, "classification parse failed — retrying once");
      return attempt("\n\n(Reminder: output ONLY the JSON object, nothing else.)");
    }
  }
}
