import { existsSync, statSync } from "node:fs";
import { rm } from "node:fs/promises";
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
import type { AgentExtensionFactory } from "../extensions/api.ts";
import type { Logger } from "../log.ts";
import type { Workspace } from "../workspace.ts";
import { type ModelTier, ModelTiers } from "./models.ts";
import { buildMainSystemPrompt } from "./prompts.ts";
import { lastAssistantText } from "./side-run.ts";

/**
 * A throwaway headless session forked from a live branch, used for non-invasive topic-shift
 * classification. `prompt` runs one agent turn against the forked conversation and returns the
 * assistant text; `dispose` tears down the session and deletes the temporary forked file.
 */
export interface ShadowFork {
  prompt(text: string): Promise<string>;
  dispose(): Promise<void>;
}

export interface ShadowForkOptions {
  systemPrompt?: string;
  tier?: ModelTier;
}

export interface AgentSessionSources {
  piFactories: AgentExtensionFactory[];
  /** Factories bound into background task runs (scoped via `app.agent.use(f, { sessionScopes: [..., "background"] })`). */
  backgroundFactories: AgentExtensionFactory[];
  /** Factories bound into delegated subagent runs that request extension tools (scoped via `app.agent.use(f, { sessionScopes: [..., "subagent"] })`). */
  subagentFactories: AgentExtensionFactory[];
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
  /**
   * Bind the registered subagent factories (their tools + resource sources) instead of no
   * factories. For delegated subagent runs that request extension tools via `delegate_to_agent`'s
   * `extensionTools` — see `app.agent.use(f, { sessionScopes: [..., "subagent"] })`. Mutually
   * exclusive with `bindBackgroundFactories` at open time; a bare session opened with neither
   * binds nothing (the usual headless side work).
   */
  bindSubagentFactories?: boolean;
}

/**
 * Which extension factories a session binds: the background subset for autonomous task runs, the
 * subagent subset for delegated runs that requested extension tools, none for other bare side
 * work, all of them otherwise. Each factory is wrapped to receive its binding session type
 * ({@link FactorySessionContext}) so it can adapt to the session it runs in. Pure so the selection
 * (and the scope it hands each factory) is unit-testable.
 *
 * Background precedence is preserved and subagent is a distinct bare-session subset — the three
 * session types are mutually exclusive at open time:
 *   bare + bindBackgroundFactories → background subset, scope "background" (unchanged)
 *   bare + bindSubagentFactories   → subagent subset,   scope "subagent"   (new)
 *   bare (neither)                 → []                                   (unchanged)
 *   non-bare                       → all piFactories,   scope "main"       (unchanged)
 */
export const selectExtensionFactories = (
  options: Pick<OpenSessionOptions, "bindBackgroundFactories" | "bindSubagentFactories" | "bare">,
  sources: Pick<AgentSessionSources, "piFactories" | "backgroundFactories" | "subagentFactories">,
): ExtensionFactory[] => {
  // A bare session is headless side work and binds nothing by default; two curated subsets are
  // opted into explicitly. background wins over subagent (precedence), then subagent, then none.
  if (options.bare === true && options.bindBackgroundFactories !== true) {
    if (options.bindSubagentFactories === true) {
      return sources.subagentFactories.map((factory) => (pi) => factory(pi, { scope: "subagent" }));
    }
    return [];
  }
  const background = options.bindBackgroundFactories === true;
  const factories = background ? sources.backgroundFactories : sources.piFactories;
  const scope = background ? "background" : "main";
  return factories.map((factory) => (pi) => factory(pi, { scope }));
};

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

  // Number of forkAndContinue runs in flight. A non-bare fork binds every pi factory, so any
  // before_agent_start work would otherwise also fire inside the memory/context post-processing
  // forks; extensions consult isForking() to scope such work to genuine top-level turns.
  private forkDepth = 0;

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
    this.tiers = new ModelTiers(config.agent, this.modelRegistry, this.settingsManager, log);
  }

  async apiKeyFor(provider: string): Promise<string | undefined> {
    return (await this.authStorage.getApiKey(provider)) ?? undefined;
  }

  /**
   * Open the main conversational session, with all registered extensions bound.
   *
   * The coordinator owns the single-active invariant and the daily-trunk lifecycle through this
   * `open` (today's trunk is one `AgentSession` reopened from its session file); pi's
   * `AgentSessionRuntime` replacement API (newSession/switchSession/fork) remains unused. Conversational
   * state lives on the pi session file as native tree entries — there is no session registry (see
   * [ADR-014](../../docs/architecture/ADR-014-session-source-of-truth.md)).
   */
  async open(options: OpenSessionOptions = {}): Promise<AgentSession> {
    const workspace = this.workspace;

    this.log.debug(
      {
        mode:
          options.inMemory === true
            ? "inMemory"
            : options.forkFromFile != null
              ? "forkFrom"
              : options.sessionFile != null
                ? "open"
                : "create",
        tier: options.tier,
        model: options.model,
        bare: options.bare === true,
      },
      "opening session",
    );

    const bare = options.bare === true;
    // The main base prompt (identity + hygiene + workspace root) is core-owned: it replaces pi's
    // coding-agent base for any non-bare session that does not bring its own system prompt. SOUL/USER
    // are layered on top by the context extension via provideContext (a before_agent_start append).
    const systemPromptOverride =
      options.systemPrompt ??
      (!bare ? buildMainSystemPrompt({ workspaceRoot: workspace.root }) : undefined);

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

    // Fire bindExtensions so pi emits resources_discover, which lets the skills extension
    // contribute the workspace skills/ directory. Without this, only npm-packaged skills
    // (like context7) are visible to the proactive classifier.
    await session.bindExtensions({});

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
    // Mark the fork in flight before open() so isForking() stays true for the whole run —
    // including the before_agent_start handlers fired during session.prompt — and the finally
    // releases the counter even if open() throws.
    this.forkDepth += 1;

    const startedAt = Date.now();

    this.log.debug({ sourceSessionFile, tier, tools }, "fork-and-continue starting");

    try {
      const session = await this.open({
        forkFromFile: sourceSessionFile,
        tier,
        ...(tools != null ? { tools } : {}),
      });

      try {
        await session.prompt(prompt);
      } catch (error) {
        this.log.error(
          { err: error instanceof Error ? error.message : String(error), sourceSessionFile },
          "fork-and-continue prompt failed",
        );

        throw error;
      } finally {
        session.dispose();
      }

      this.log.debug(
        { sourceSessionFile, tier, durationMs: Date.now() - startedAt },
        "fork-and-continue finished",
      );
    } finally {
      this.forkDepth -= 1;
    }
  }

  /** Whether any forkAndContinue run is currently in flight (see `forkDepth`). */
  isForking(): boolean {
    return this.forkDepth > 0;
  }

  /**
   * Load `sourceSessionFile` into a SEPARATE `SessionManager` — never a live session's manager — so
   * that pi's in-place `createBranchedSession` rewrite cannot mutate a session the caller still holds.
   * Centralizes the one rule that keeps branch-file creation non-destructive (see {@link branchFile}).
   */
  private loadDetachedSession(sourceSessionFile: string): SessionManager {
    return SessionManager.open(sourceSessionFile, this.workspace.sessionsDir);
  }

  /**
   * Write a throwaway session file holding only the root→`leafId` path of `sourceSessionFile`, and
   * return its path (undefined if the source is not persisting). The source transcript is never
   * mutated.
   *
   * FOOTGUN: pi's `SessionManager.createBranchedSession` is destructive IN PLACE — it repoints the
   * manager it runs on at the new branch file and rebuilds that manager's entry index from only the
   * branch path. Running it on a *live* session's manager therefore silently turns that session into
   * the branch: every other branch becomes unreachable (the next `createBranchedSession` throws
   * "Entry not found") and later appends — including idempotency markers — land in the wrong file.
   * We always run it on a manager loaded fresh from disk. See `docs/reference/pi-sdk-notes.md`.
   */
  branchFile(sourceSessionFile: string, leafId: string): string | undefined {
    return this.loadDetachedSession(sourceSessionFile).createBranchedSession(leafId);
  }

  /**
   * Fork the current branch of `sourceSessionFile` into a throwaway headless session for
   * non-invasive classification (topic-shift detection). We open the source file in a SEPARATE
   * `SessionManager` and `createBranchedSession(leafId)` a fresh file containing only the
   * root→leaf path — the source transcript is never mutated (R6). The forked session is opened
   * bare (no extensions — so the boundary extension does not recursively load in the shadow) with
   * no tools and the live system prompt, then deleted on dispose.
   *
   * Reuses the existing `open()` machinery rather than pi's lower-level
   * `createAgentSessionServices`/`FromServices` two-call path (see design S2): `open` already
   * composes the loader, model tier, and tool allowlist we need, and `bare` + `tools: []` give an
   * extension-free, tool-free headless session equivalent to `noTools: "all"`.
   */
  async shadowFork(
    sourceSessionFile: string,
    options: ShadowForkOptions = {},
  ): Promise<ShadowFork> {
    const source = this.loadDetachedSession(sourceSessionFile);
    const leafId = source.getLeafId();
    if (leafId == null) throw new Error("cannot shadow-fork a session with no entries");

    const forkedFile = source.createBranchedSession(leafId);
    if (forkedFile == null) throw new Error("shadow fork did not persist a branched session file");

    const tier = options.tier ?? "classifier";

    const session = await this.open({
      sessionFile: forkedFile,
      bare: true,
      tier,
      tools: [],
      ...(options.systemPrompt != null ? { systemPrompt: options.systemPrompt } : {}),
    });

    this.log.debug({ sourceSessionFile, forkedFile, tier }, "shadow fork created");

    return {
      prompt: async (text) => {
        try {
          await session.prompt(text);
        } catch (error) {
          this.log.warn(
            { err: error instanceof Error ? error.message : String(error), forkedFile },
            "shadow fork prompt failed",
          );

          throw error;
        }

        return lastAssistantText(session.messages);
      },
      dispose: async () => {
        session.dispose();
        await rm(forkedFile, { force: true });
      },
    };
  }
}
