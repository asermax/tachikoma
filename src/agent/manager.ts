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
} from "@earendil-works/pi-coding-agent";

import type { Config } from "../config/schema.ts";
import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { type ModelTier, ModelTiers } from "./models.ts";

export interface AgentSessionSources {
  piFactories: ExtensionFactory[];
  systemPromptBuilders: (() => string)[];
}

export interface OpenSessionOptions {
  /** Resume from an existing pi session file instead of starting fresh. */
  sessionFile?: string | null;
  /** Ephemeral side session: nothing persisted to disk. */
  inMemory?: boolean;
  /** Skip registered extension factories and system prompt builders (headless side work). */
  bare?: boolean;
  tools?: string[];
  systemPrompt?: string;
  tier?: ModelTier;
}

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

  /** Open the main conversational session, with all registered extensions bound. */
  async open(options: OpenSessionOptions = {}): Promise<AgentSession> {
    const workspace = this.workspace;

    const bare = options.bare === true;
    const systemPromptOverride =
      options.systemPrompt ??
      (!bare && this.sources.systemPromptBuilders.length > 0
        ? this.composeSystemPrompt()
        : undefined);

    const loader = new DefaultResourceLoader({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      extensionFactories: bare ? [] : [...this.sources.piFactories],
      ...(systemPromptOverride != null ? { systemPromptOverride: () => systemPromptOverride } : {}),
    });
    await loader.reload();

    const sessionManager =
      options.inMemory === true
        ? SessionManager.inMemory(workspace.root)
        : options.sessionFile != null
          ? SessionManager.open(options.sessionFile, workspace.sessionsDir)
          : SessionManager.create(workspace.root, workspace.sessionsDir);

    // A configured role pins the model (and optional thinking suffix); an unset
    // chain omits both so pi's full resolution applies — session restore first,
    // then settings defaults, then the first credentialed model.
    const tier = options.tier ?? "main";
    const configured = this.tiers.configuredRef(tier) != null ? this.tiers.resolve(tier) : null;

    const { session, modelFallbackMessage } = await createAgentSession({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      ...(configured != null ? { model: configured.model } : {}),
      ...(configured?.thinkingLevel != null ? { thinkingLevel: configured.thinkingLevel } : {}),
      ...(options.tools != null ? { tools: options.tools } : {}),
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

  private composeSystemPrompt(): string {
    return this.sources.systemPromptBuilders.map((build) => build()).join("\n\n");
  }
}
