import { type AssistantMessage, completeSimple, type Message } from "@earendil-works/pi-ai";
import type { AgentSession, ToolDefinition } from "@earendil-works/pi-coding-agent";
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
  /** Aborts the underlying provider request; a classify that times out passes its signal here. */
  signal?: AbortSignal;
  /** Output token cap (default 256 — classifications are tiny JSON objects). */
  maxTokens?: number;
  /** Sampling temperature (default 0 for deterministic classification). */
  temperature?: number;
}

export interface CompleteOptions {
  system?: string;
  user: string;
  tier?: ModelTier;
  /** Cap output tokens, forwarded to the provider. */
  maxTokens?: number;
  /** Sampling temperature, forwarded to the provider. */
  temperature?: number;
  /** Aborts the underlying provider request. */
  signal?: AbortSignal;
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

export interface BackgroundSessionOptions {
  system: string;
  customTools: ToolDefinition[];
  /** Resume from an existing pi session file instead of starting fresh. */
  sessionFile?: string | null;
  tier?: ModelTier;
}

const textOf = (message: AssistantMessage): string =>
  message.content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("");

/** Last assistant turn's text from a session's message log. */
export const lastAssistantText = (messages: readonly { role: string }[]): string => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message == null || message.role !== "assistant") continue;
    if (!Array.isArray((message as { content?: unknown }).content)) continue;

    return textOf(message as AssistantMessage);
  }

  return "";
};

/**
 * Pull the first complete top-level JSON object out of model output. A fenced
 * block wins; otherwise a string-aware brace scan from the first `{` to its
 * match ignores any trailing prose or a second concatenated object — exactly the
 * shapes that surfaced as `Unexpected non-whitespace character after JSON`.
 */
export const extractJson = (text: string): string => {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fenced?.[1] != null) return fenced[1].trim();

  const start = text.indexOf("{");
  if (start === -1) return text.trim();

  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = start; index < text.length; index += 1) {
    const char = text[index];

    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }

    if (char === '"') inString = true;
    else if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(start, index + 1);
    }
  }

  // Unbalanced — fall back to everything from the first brace so JSON.parse
  // surfaces a precise error rather than silently swallowing the mismatch.
  return text.slice(start).trim();
};

/** Side-channel LLM work outside the conversational session: classification and one-shot completions. */
export class SideRunner {
  private readonly manager: AgentManager;
  private readonly log: Logger;

  constructor(manager: AgentManager, log: Logger) {
    this.manager = manager;
    this.log = log;
  }

  async complete({
    system,
    user,
    tier = "processor",
    maxTokens,
    temperature,
    signal,
  }: CompleteOptions): Promise<string> {
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

    // Build the provider options only from what was given, passing `undefined` when
    // nothing is set (so the provider sees no options object at all, matching prior
    // behavior). maxTokens/temperature/signal are honored by pi-ai's OpenAI-compatible
    // providers (e.g. the GLM/ZAI classifier), including a signal-driven cancellation.
    const options: {
      apiKey?: string;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    } = {};
    if (apiKey != null) options.apiKey = apiKey;
    if (maxTokens != null) options.maxTokens = maxTokens;
    if (temperature != null) options.temperature = temperature;
    if (signal != null) options.signal = signal;

    const result = await completeSimple(
      model,
      { ...(system != null ? { systemPrompt: system } : {}), messages },
      Object.keys(options).length > 0 ? options : undefined,
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

      return { text: lastAssistantText(session.messages) };
    } finally {
      session.dispose();
    }
  }

  /**
   * Open a PERSISTENT pi session for an autonomous background task: a session file is written
   * under the workspace sessions dir, the curated background factories are bound, and the given
   * custom tools are active — with NO `tools` allowlist (a hard allowlist filters out
   * extension/custom tools). The caller prompts the returned session repeatedly across the
   * evaluator loop and disposes it; `session.sessionFile` is the path to persist for resumption.
   */
  async openBackgroundSession({
    system,
    customTools,
    sessionFile,
    tier = "processor",
  }: BackgroundSessionOptions): Promise<AgentSession> {
    return this.manager.open({
      tier,
      bindBackgroundFactories: true,
      systemPrompt: system,
      customTools,
      ...(sessionFile != null ? { sessionFile } : {}),
    });
  }

  /**
   * Structured classification: instructs JSON output matching the schema, parses, retries once.
   * Output is capped and sampled at temperature 0 (overridable), and an abort signal is forwarded
   * so a slow classify is cancelled at its deadline rather than left racing.
   */
  async classify<S extends TSchema>({
    system,
    user,
    schema,
    tier = "classifier",
    signal,
    maxTokens = 256,
    temperature = 0,
  }: ClassifyOptions<S>): Promise<Static<S>> {
    const instruction = `${system}\n\nRespond with a single JSON object matching this JSON Schema — no prose:\n${JSON.stringify(schema)}`;

    const attempt = async (extra: string): Promise<Static<S>> => {
      const text = await this.complete({
        system: instruction,
        user: `${user}${extra}`,
        tier,
        maxTokens,
        temperature,
        signal,
      });
      return parseWithSchema(schema, JSON.parse(extractJson(text)), "classification output");
    };

    try {
      return await attempt("");
    } catch (error) {
      // A deadline abort should not burn a second, already-aborted attempt.
      if (signal?.aborted) throw error;
      this.log.debug({ err: error }, "classification parse failed — retrying once");
      return attempt("\n\n(Reminder: output ONLY the JSON object, nothing else.)");
    }
  }
}
