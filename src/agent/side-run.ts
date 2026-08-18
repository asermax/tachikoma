import type { AssistantMessage, Message } from "@earendil-works/pi-ai";
import { completeSimple } from "@earendil-works/pi-ai/compat";
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
  /**
   * Exposed extension tool names to grant this run, additive on top of `tools` (the resolved
   * built-ins). Empty/omitted = none granted (takes the built-in-allowlist path unchanged). Each
   * name is resolved source-agnostically against the opened session at execute time — a name that
   * does not register throws a self-correcting error before the run starts (see DLT-184).
   */
  extensionTools?: string[];
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

/**
 * pi's built-in tool names — used to tell granted extension tools apart from built-ins when listing
 * the valid names in an unresolved-extension error. Mirrors the set the `delegate_to_agent` `tools`
 * param validates against (`src/extensions/skills/delegate.ts`); duplicated here because the agent
 * layer does not import from the skills extension. Reconcile into one shared source if pi's
 * built-in surface changes.
 */
const BUILTIN_TOOL_NAMES = new Set(["read", "grep", "find", "ls", "bash", "edit", "write"]);

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

    this.log.debug({ tier, model: `${model.provider}/${model.id}` }, "side completion starting");

    const startedAt = Date.now();

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
      this.log.warn(
        {
          tier,
          model: `${model.provider}/${model.id}`,
          stopReason: result.stopReason,
          errorMessage: result.errorMessage,
        },
        "side completion failed",
      );

      throw new Error(`side completion failed: ${result.errorMessage ?? result.stopReason}`);
    }

    this.log.debug(
      { tier, model: `${model.provider}/${model.id}`, durationMs: Date.now() - startedAt },
      "side completion finished",
    );

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
    extensionTools,
  }: HeadlessRunOptions): Promise<HeadlessRunResult> {
    // A non-empty `extensionTools` takes the grant path: bind the subagent-scoped factories and
    // resolve the requested names source-agnostically against the opened session (see DLT-184). An
    // empty/omitted list keeps the unchanged built-in-allowlist path.
    const requestedExtensions =
      extensionTools != null && extensionTools.length > 0 ? extensionTools : undefined;

    this.log.debug(
      { tier, model, tools, backgroundExtensions, extensionTools },
      "headless run starting",
    );

    const startedAt = Date.now();

    // A hard `tools` allowlist also filters out extension/custom tools, so a run that binds
    // factories — a background run OR an extension-grant run — must NOT set one: the bound factory
    // tools would otherwise be masked (the double-block). The grant run narrows the active set to
    // the resolved built-ins plus granted tools via setActiveToolsByName after open; other runs
    // keep an explicit built-in allowlist (plus their custom tools' names).
    const dropToolAllowlist = backgroundExtensions === true || requestedExtensions != null;

    const session = await this.manager.open({
      inMemory: true,
      bare: true,
      tier,
      ...(model != null ? { model } : {}),
      ...(isolatePrompt === true ? { isolatePrompt: true } : {}),
      ...(backgroundExtensions === true ? { bindBackgroundFactories: true } : {}),
      ...(requestedExtensions != null ? { bindSubagentFactories: true } : {}),
      ...(dropToolAllowlist
        ? {}
        : { tools: [...tools, ...(customTools ?? []).map((tool) => tool.name)] }),
      ...(customTools != null ? { customTools } : {}),
      ...(system != null ? { systemPrompt: system } : {}),
    });

    try {
      if (requestedExtensions != null) {
        // Resolve every requested name against the session's full registry (Tachikoma-factory and
        // pi-native tools alike — source-agnostic), then narrow the active set to exactly the
        // resolved built-ins plus the granted tools. An unresolved name throws a self-correcting
        // error BEFORE the prompt runs, steering the model to a valid name (no run is attempted).
        // getAllTools/setActiveToolsByName are synchronous on the installed SDK (no await).
        const allTools = session.getAllTools();
        const available = new Set(allTools.map((tool) => tool.name));
        const missing = requestedExtensions.filter((name) => !available.has(name));
        if (missing.length > 0) {
          const grantable = allTools
            .map((tool) => tool.name)
            .filter((name) => !BUILTIN_TOOL_NAMES.has(name));
          const grantableText =
            grantable.length > 0 ? grantable.join(", ") : "(none are currently exposed)";
          throw new Error(
            `Unknown extension tools for delegate_to_agent: ${missing.join(", ")}. Exposed extension tools you can request via \`extensionTools\`: ${grantableText}.`,
          );
        }
        // setActiveToolsByName rebuilds the system prompt to reflect the new tool set; the change
        // takes effect on the next turn (`session.prompt` below).
        session.setActiveToolsByName([...tools, ...requestedExtensions]);
      }

      await session.prompt(prompt);

      this.log.debug({ tier, durationMs: Date.now() - startedAt }, "headless run finished");

      return { text: lastAssistantText(session.messages) };
    } catch (error) {
      this.log.warn(
        { err: error instanceof Error ? error.message : String(error), tier },
        "headless run failed",
      );

      throw error;
    } finally {
      session.dispose();
    }
  }

  /**
   * Open a PERSISTENT pi session for an autonomous background task: a session file is written
   * under the workspace sessions dir, the curated background factories are bound, and the given
   * custom tools are active — with NO `tools` allowlist (a hard allowlist filters out
   * extension/custom tools). The caller prompts the returned session repeatedly across the
   * run loop and disposes it; `session.sessionFile` is the path to persist for resumption.
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

      try {
        return await attempt("\n\n(Reminder: output ONLY the JSON object, nothing else.)");
      } catch (retryError) {
        this.log.warn(
          { err: retryError instanceof Error ? retryError.message : String(retryError), tier },
          "classification failed after retry",
        );

        throw retryError;
      }
    }
  }
}
