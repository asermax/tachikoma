import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import {
  type AgentSession,
  AuthStorage,
  createAgentSession,
  DefaultResourceLoader,
  type ExtensionFactory,
  ModelRegistry,
  SessionManager,
  SettingsManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";

import type { Config } from "../config/schema.ts";
import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { type ModelTier, ModelTiers } from "./models.ts";

export interface AgentSessionSources {
  piFactories: ExtensionFactory[];
  /** Factories bound into background task runs (scoped via `app.agent.use(f, { sessionScopes: [..., "background"] })`). */
  backgroundFactories: ExtensionFactory[];
  systemPromptBuilders: (() => string)[];
}

export interface OpenSessionOptions {
  /** Resume from an existing pi session file instead of starting fresh. */
  sessionFile?: string | null;
  /**
   * Fork a NEW session seeded with the full history of an existing pi session file. The source
   * transcript is read-only (never mutated): `SessionManager.forkFrom` copies its entries into a
   * fresh session file. Used by memory extraction to continue the just-ended conversation as the
   * same assistant. Must be opened on the normal (non-bare) path so the fork keeps the composed
   * persona and the agent's own tool set.
   */
  forkFromFile?: string | null;
  /** Ephemeral side session: nothing persisted to disk. */
  inMemory?: boolean;
  /** Skip registered extension factories and system prompt builders (headless side work). */
  bare?: boolean;
  tools?: string[];
  customTools?: ToolDefinition[];
  systemPrompt?: string;
  tier?: ModelTier;
  /** Explicit "provider/model-id[:thinkingLevel]" reference; pins the model over `tier`. */
  model?: string;
  /**
   * Fully isolate the system prompt: suppress pi's APPEND_SYSTEM.md auto-append, project context
   * files (AGENTS.md/CLAUDE.md), and the skills catalog, so the session sees exactly its own
   * `systemPrompt` (plus pi's date/cwd footer). For delegated subagents with self-contained prompts.
   */
  isolatePrompt?: boolean;
  /**
   * Bind the registered background factories (their tools + resource sources) instead of no
   * factories. For autonomous background task runs that need a curated slice of the agent's
   * capabilities (skills, git, projects, etc.) — see `app.agent.use(f, { sessionScopes: [..., "background"] })`.
   */
  bindBackgroundFactories?: boolean;
}

/**
 * Which extension factories a session binds: the background subset for autonomous task runs, none
 * for other bare side work, all of them otherwise. Pure so the selection is unit-testable.
 */
export const selectExtensionFactories = (
  options: Pick<OpenSessionOptions, "bindBackgroundFactories" | "bare">,
  sources: Pick<AgentSessionSources, "piFactories" | "backgroundFactories">,
): ExtensionFactory[] =>
  options.bindBackgroundFactories === true
    ? [...sources.backgroundFactories]
    : options.bare === true
      ? []
      : [...sources.piFactories];

/**
 * Loader options that strip everything pi would otherwise graft onto a custom system prompt.
 * Extracted so the suppression set is unit-testable without constructing a real loader.
 */
export const isolatedLoaderOptions = () => ({
  appendSystemPromptOverride: () => [],
  noContextFiles: true,
  noSkills: true,
});

export class AgentManager {
  readonly authStorage: AuthStorage;
  readonly modelRegistry: ModelRegistry;
  readonly settingsManager: SettingsManager;
  readonly tiers: ModelTiers;

  private readonly workspace: Workspace;
  private readonly sources: AgentSessionSources;
  private readonly log: Logger;

  constructor(workspace: Workspace, config: Config, sources: AgentSessionSources, log: Logger) {
    this.workspace = workspace;
    this.sources = sources;
    this.log = log;

    // Credentials are machine-level: prefer a workspace-local auth.json when it has
    // actual content, otherwise share the user's existing pi login (~/.pi/agent/auth.json).
    const localAuth = join(workspace.piDir, "auth.json");
    const hasLocalAuth = existsSync(localAuth) && statSync(localAuth).size > 2;
    this.authStorage = hasLocalAuth ? AuthStorage.create(localAuth) : AuthStorage.create();
    this.modelRegistry = ModelRegistry.create(
      this.authStorage,
      join(workspace.piDir, "models.json"),
    );
    this.settingsManager = SettingsManager.create(workspace.root, workspace.piDir);
    this.tiers = new ModelTiers(config.agent, this.modelRegistry, this.settingsManager);
  }

  async apiKeyFor(provider: string): Promise<string | undefined> {
    return (await this.authStorage.getApiKey(provider)) ?? undefined;
  }

  /**
   * Open the main conversational session, with all registered extensions bound.
   *
   * We open a fresh AgentSession per topic via createAgentSession and deliberately bypass pi's
   * AgentSessionRuntime replacement API (newSession/switchSession/fork): Tachikoma owns session
   * lifecycle and resumption through its drizzle-backed session registry, not pi's session tree.
   */
  async open(options: OpenSessionOptions = {}): Promise<AgentSession> {
    const workspace = this.workspace;

    const bare = options.bare === true;
    const systemPromptOverride =
      options.systemPrompt ??
      (!bare && this.sources.systemPromptBuilders.length > 0
        ? this.composeSystemPrompt()
        : undefined);

    const extensionFactories = selectExtensionFactories({ ...options, bare }, this.sources);

    const loader = new DefaultResourceLoader({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      extensionFactories,
      ...(systemPromptOverride != null ? { systemPromptOverride: () => systemPromptOverride } : {}),
      ...(options.isolatePrompt === true ? isolatedLoaderOptions() : {}),
    });
    await loader.reload();

    const sessionManager =
      options.inMemory === true
        ? SessionManager.inMemory(workspace.root)
        : options.forkFromFile != null
          ? SessionManager.forkFrom(options.forkFromFile, workspace.root, workspace.sessionsDir)
          : options.sessionFile != null
            ? SessionManager.open(options.sessionFile, workspace.sessionsDir)
            : SessionManager.create(workspace.root, workspace.sessionsDir);

    // An explicit model reference pins the model directly; otherwise a configured
    // role pins the model (and optional thinking suffix). An unset chain omits both
    // so pi's full resolution applies — session restore first, then settings
    // defaults, then the first credentialed model.
    const tier = options.tier ?? "main";
    const configured =
      options.model != null
        ? this.tiers.resolveRef(options.model)
        : this.tiers.configuredRef(tier) != null
          ? this.tiers.resolve(tier)
          : null;

    const { session, modelFallbackMessage } = await createAgentSession({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      ...(configured != null ? { model: configured.model } : {}),
      ...(configured?.thinkingLevel != null ? { thinkingLevel: configured.thinkingLevel } : {}),
      ...(options.tools != null ? { tools: options.tools } : {}),
      ...(options.customTools != null ? { customTools: options.customTools } : {}),
      resourceLoader: loader,
      sessionManager,
      settingsManager: this.settingsManager,
      authStorage: this.authStorage,
      modelRegistry: this.modelRegistry,
    });

    if (modelFallbackMessage != null) {
      this.log.warn({ modelFallbackMessage }, "model fallback on session open");
    }

    return session;
  }

  /**
   * Fork the conversation in `sourceSessionFile` into a fresh session and continue it headlessly:
   * the same assistant (composed persona + full history live) is handed `prompt` as a single
   * follow-up user turn, run to completion, and disposed. The source transcript is never mutated.
   *
   * `tools` is an optional hard allowlist. We stay non-bare so the persona and history survive, but
   * a `tools` allowlist still restricts the fork to exactly those built-in tool names (the SDK's
   * `tools` option is independent of the system prompt and also filters out extension tools) — so a
   * memory-extraction fork keeps the assistant but can only touch files, never messaging/task tools.
   */
  async forkAndContinue(
    sourceSessionFile: string,
    prompt: string,
    tier: ModelTier,
    tools?: string[],
  ): Promise<void> {
    const session = await this.open({
      forkFromFile: sourceSessionFile,
      tier,
      ...(tools != null ? { tools } : {}),
    });

    try {
      await session.prompt(prompt);
    } finally {
      session.dispose();
    }
  }

  private composeSystemPrompt(): string {
    return this.sources.systemPromptBuilders.map((build) => build()).join("\n\n");
  }
}
