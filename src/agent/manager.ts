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
import { ModelTiers } from "./models.ts";

export interface AgentSessionSources {
  piFactories: ExtensionFactory[];
  systemPromptBuilders: (() => string)[];
}

export interface OpenSessionOptions {
  /** Resume from an existing pi session file instead of starting fresh. */
  sessionFile?: string | null;
  /** Ephemeral side session: nothing persisted to disk. */
  inMemory?: boolean;
  tools?: string[];
  systemPrompt?: string;
}

export class AgentManager {
  readonly authStorage: AuthStorage;
  readonly modelRegistry: ModelRegistry;
  readonly tiers: ModelTiers;

  private readonly workspace: Workspace;
  private readonly config: Config;
  private readonly sources: AgentSessionSources;
  private readonly log: Logger;

  constructor(workspace: Workspace, config: Config, sources: AgentSessionSources, log: Logger) {
    this.workspace = workspace;
    this.config = config;
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
    this.tiers = new ModelTiers(config.agent, this.modelRegistry);
  }

  async apiKeyFor(provider: string): Promise<string | undefined> {
    return (await this.authStorage.getApiKey(provider)) ?? undefined;
  }

  /** Open the main conversational session, with all registered extensions bound. */
  async open(options: OpenSessionOptions = {}): Promise<AgentSession> {
    const workspace = this.workspace;

    const loader = new DefaultResourceLoader({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      extensionFactories: [...this.sources.piFactories],
      ...(this.sources.systemPromptBuilders.length > 0
        ? { systemPromptOverride: () => this.composeSystemPrompt() }
        : {}),
    });
    await loader.reload();

    const sessionManager =
      options.inMemory === true
        ? SessionManager.inMemory(workspace.root)
        : options.sessionFile != null
          ? SessionManager.open(options.sessionFile, workspace.sessionsDir)
          : SessionManager.create(workspace.root, workspace.sessionsDir);

    const { session, modelFallbackMessage } = await createAgentSession({
      cwd: workspace.root,
      agentDir: workspace.piDir,
      model: this.tiers.resolve("agent"),
      thinkingLevel: this.config.agent.thinkingLevel,
      ...(options.tools != null ? { tools: options.tools } : {}),
      resourceLoader: loader,
      sessionManager,
      settingsManager: SettingsManager.create(workspace.root, workspace.piDir),
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
