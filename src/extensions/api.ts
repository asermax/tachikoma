import type { AgentSession, ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { TSchema } from "typebox";
import type { ShadowFork, ShadowForkOptions } from "../agent/manager.ts";
import type { ModelTier, ModelTiers } from "../agent/models.ts";
import type { SideRunner } from "../agent/side-run.ts";
import type { Channel, Delivery } from "../channels/types.ts";
import type { Config } from "../config/schema.ts";
import type { AppDatabase } from "../db/index.ts";
import type { KeyValueState } from "../db/state.ts";
import type { InboundMessage } from "../domain/message.ts";
import type { EventBus } from "../events.ts";
import type { CommitAllDeterministicOptions, CommitAllOptions } from "../git/commit.ts";
import type { CommitAgent } from "../git/commit-agent.ts";
import type { PushResult, RebaseResolver, SyncResult } from "../git/sync.ts";
import type { Logger } from "../log.ts";
import type { Scheduler } from "../scheduler.ts";
import type { AutoDecision, BranchRecord } from "../sessions/trunk.ts";
import type { Workspace } from "../workspace.ts";

// ---- pipeline contracts -----------------------------------------------------

export interface ExchangeContext {
  userText: string;
}

export type ExchangeProcessor = {
  name: string;
  process(context: ExchangeContext): Promise<void>;
};

export const POST_PROCESSING_PHASES = {
  main: "main",
  preFinalize: "preFinalize",
  finalize: "finalize",
} as const;

export type PostProcessingPhase = keyof typeof POST_PROCESSING_PHASES;

/** The day's trunk handed to the close pipeline (daily-trunk model). */
export interface TrunkPostContext {
  session: AgentSession;
  sessionFile: string;
  /** Local calendar day (`YYYY-MM-DD`) the trunk belongs to. */
  day: string;
  branchRecords: BranchRecord[];
}

export interface PostProcessorContext {
  /** The closing trunk, or null for background runs that have no conversational trunk. */
  trunk: TrunkPostContext | null;
  /** Path to the pi session JSONL transcript (the trunk session file), when one persisted. */
  transcriptPath: string | null;
  log: Logger;
}

export interface PostProcessor {
  name: string;
  phase?: PostProcessingPhase;
  /**
   * Friendly label for the user-facing "in progress" status line shown while this processor runs
   * (e.g. "Processing memories"). Omit to fall back to the generic "Post-processing: <name>".
   */
  statusLabel?: string;
  process(context: PostProcessorContext): Promise<void>;
}

/** The live trunk handed to inbound middleware so the boundary can drive collapse on it directly. */
export interface TrunkInbound {
  session: AgentSession;
  sessionFile: string;
  /** The base the live branch extends (latest collapse summary id), or null on a fresh trunk. */
  currentBaseId: string | null;
  branchRecords: BranchRecord[];
  /** The id the live branch will carry if it collapses (matches the eventual `getBranchRecords` id). */
  liveBranchId: string;
  /** Whether the live branch has at least one assistant turn since its base (empty-branch guard). */
  hasAssistantTurnSinceBase: boolean;
  /** The active checkpoint's main-line tip entry id, or null when none is set (DLT-181). */
  checkpointId: string | null;
  /** Whether an active checkpoint marks a returnable main-line point (checkpointId != null). */
  checkpointActive: boolean;
  /** The most recent automatic branching decision (a `/rollback` target), or null. */
  lastAutoDecision: AutoDecision | null;
}

export interface InboundContext {
  /** The live trunk, or null when no trunk is active yet (e.g. a fully-handled command). */
  trunk: TrunkInbound | null;
}

export type InboundMiddleware = (
  message: InboundMessage,
  context: InboundContext,
  next: () => Promise<void>,
) => Promise<void>;

// ---- app services exposed to extensions --------------------------------------

export interface SessionsApi {
  /** Close the active trunk immediately — callers must know no exchange is streaming. */
  close(): Promise<void>;
  /** Abort the in-flight agent run, if any (user-initiated stop). */
  abortExchange(): Promise<void>;
  /** The live daily-trunk pi session, or null when no trunk is active. */
  activeTrunkSession(): AgentSession | null;
  /** Fires once when the day's trunk opens (daily-trunk lifecycle). */
  onOpen(hook: () => void | Promise<void>): void;
  onExchange(processor: ExchangeProcessor): void;
  registerProcessor(processor: PostProcessor): void;
  /**
   * Run the registered post-processors once for a headless/background run, in phase order and
   * error-isolated. Processors that require a transcript no-op when `transcriptPath` is null.
   */
  runPostProcessors(context: PostProcessorContext): Promise<void>;
}

export interface ChannelsApi {
  register(channel: Channel): void;
  deliver(delivery: Delivery): void;
}

/** Session contexts a tool/resource factory can bind into. */
export const SESSION_SCOPES = {
  /** The main conversational session. */
  main: "main",
  /** Autonomous background task runs. */
  background: "background",
} as const;

export type SessionScope = (typeof SESSION_SCOPES)[keyof typeof SESSION_SCOPES];

/**
 * The session a factory is being bound into, handed to every factory so it can adapt to its binding
 * context (e.g. suppress user-facing surfaces in a background session). The scope matches the
 * `sessionScopes` membership the factory was registered with; `selectExtensionFactories` supplies it.
 */
export interface FactorySessionContext {
  scope: SessionScope;
}

/**
 * A Tachikoma extension factory: like pi's `ExtensionFactory` (`(pi) => void`) but receives the
 * {@link FactorySessionContext} as a second argument. A factory written as `(pi) => …` (or returned by
 * `provideContext` / a `createXxxFactory` helper) is still valid — the session argument is optional at the
 * call site, so existing factories bind unchanged and simply ignore it.
 */
export type AgentExtensionFactory = (
  pi: ExtensionAPI,
  session: FactorySessionContext,
) => void | Promise<void>;

export interface UseFactoryOptions {
  /**
   * Which session contexts this factory binds into (default `["main"]`). Each scope is
   * independent: include `"main"` to bind the conversational session, `"background"` to
   * bind autonomous task runs, both to bind both, or `"background"` alone for a factory only
   * background runs should see. Binding is by membership, so an out-of-union scope binds
   * nothing rather than throwing. Use `"background"` for tools a background task legitimately
   * needs (skills, git, projects, detached processes, notifications, task management).
   */
  sessionScopes?: SessionScope[];
}

export interface AgentApi {
  /**
   * Contribute a pi extension factory, scoped to the given sessions. The factory receives its
   * binding {@link FactorySessionContext} so it can adapt to the session it runs in. Context
   * sections use this same form via `provideContext(provide, customType?)` as the factory: with no
   * `customType` the content is appended to the system prompt, with a `customType` it is injected
   * as a hidden message.
   */
  use(factory: AgentExtensionFactory, options?: UseFactoryOptions): void;
  readonly models: ModelTiers;
  /** Side-channel LLM work: headless runs and structured classification. */
  readonly side: SideRunner;
  /**
   * Fork the conversation in `sourceSessionFile` into a fresh session and continue it headlessly:
   * the same assistant (composed persona + full history live) is handed `prompt` as one follow-up
   * user turn, run to completion, and disposed. The source transcript is never mutated. `tools` is
   * an optional hard allowlist restricting the fork to those built-in tool names. Used by memory
   * extraction to fold a just-ended conversation into the memory store.
   */
  forkAndContinue(
    sourceSessionFile: string,
    prompt: string,
    tier: ModelTier,
    tools?: string[],
  ): Promise<void>;
  /**
   * Whether a `forkAndContinue` run is currently in flight. A non-bare fork binds every pi
   * factory, so a `before_agent_start` handler also fires inside the memory/context
   * post-processing forks; consult this to scope such per-turn work to genuine top-level turns.
   */
  isForking(): boolean;
  /**
   * Fork the current branch of `sourceSessionFile` into a throwaway headless session for
   * non-invasive topic-shift classification — the source transcript is never mutated. The returned
   * handle runs one classification turn and is disposed (which deletes the forked file). See the
   * `session-tree` helpers for direct branch/tree access on a live session.
   */
  shadowFork(sourceSessionFile: string, options?: ShadowForkOptions): Promise<ShadowFork>;
  /**
   * Write a throwaway session file holding only the root→`leafId` path of `sourceSessionFile` (the
   * full conversation of one collapsed branch), returning its path or undefined if the source is not
   * persisting. The source transcript — and any live session pointed at it — is never mutated: the
   * branch is cut from a manager loaded fresh from disk. Used by per-branch memory extraction and
   * `ask_branch` to fork one prior branch's own turns.
   */
  branchFile(sourceSessionFile: string, leafId: string): string | undefined;
}

export interface InboundApi {
  use(middleware: InboundMiddleware): void;
}

/**
 * High-level git operations over the core git helpers (`src/git/`), exposed as a
 * neutral service so extensions consume them through `app` instead of importing
 * the git extension. The git extension itself owns workspace versioning, the
 * agent-facing tools, the bash guardrail, and the commit post-processor; those
 * remain its responsibility and are not part of this surface.
 */
export interface GitApi {
  /**
   * Commit every change in `cwd` via the agent-driven grouped flow: the agent
   * runs first, and `fallbackMessage` backs a single commit only if it fails or
   * leaves the tree dirty. Returns the subjects of every commit made, or an
   * empty array when there was nothing to commit. `log` defaults to the
   * extension's logger when omitted.
   */
  commitAll(options: Omit<CommitAllOptions, "log"> & { log?: Logger }): Promise<string[]>;
  /**
   * Commit every change in `cwd` in one deterministic commit (`git add -A` +
   * commit `message`) — no agent, no model call. Returns the subjects of every
   * commit made, or an empty array when the tree was clean. `log` defaults to
   * the extension's logger when omitted.
   */
  commitAllDeterministic(
    options: Omit<CommitAllDeterministicOptions, "log"> & { log?: Logger },
  ): Promise<string[]>;
  /**
   * Build a `CommitAgent` for a repo. `"workspace"` groups workspace changes by
   * area; `"project"` matches the target repo's own commit-message style.
   */
  createCommitAgent(mode: "workspace" | "project"): CommitAgent;
  /** Push local commits with divergence recovery (fetch → detect → push or rebase-then-push). */
  smartPush(
    cwd: string,
    remote: string,
    branch: string,
    options?: { log?: Logger; resolver?: RebaseResolver },
  ): Promise<PushResult>;
  /** Pull remote changes with divergence recovery (skip when dirty, fast-forward or rebase). */
  smartPull(
    cwd: string,
    remote: string,
    branch: string,
    options?: { log?: Logger; resolver?: RebaseResolver },
  ): Promise<SyncResult>;
}

export interface AppContext<C = unknown> {
  readonly config: Config;
  readonly extensionConfig: C;
  readonly workspace: Workspace;
  readonly log: Logger;
  readonly db: AppDatabase;
  readonly state: KeyValueState;
  readonly events: EventBus;
  readonly scheduler: Scheduler;
  readonly sessions: SessionsApi;
  readonly channels: ChannelsApi;
  readonly agent: AgentApi;
  readonly inbound: InboundApi;
  readonly git: GitApi;

  bootstrap(name: string, hook: () => void | Promise<void>): void;
  /**
   * Register a hook that runs once during shutdown, before the coordinator's final
   * delivery flush, so a hook can push any held output into that flush. Error-isolated.
   */
  onShutdown(name: string, hook: () => void | Promise<void>): void;
  /** Surface a progress line through the active channel while processing. */
  status(text: string): void;
  /**
   * Enqueue a third-party extension for loading (external extension support).
   * The host isolates these: a throwing or hanging `setup` is logged and skipped
   * rather than aborting startup. `setupTimeoutMs` bounds the hang guard.
   */
  registerExtension(extension: TachikomaExtension<never>, options?: RegisterExtensionOptions): void;
}

export interface RegisterExtensionOptions {
  /** Milliseconds before an external `setup` is considered hung and skipped. */
  setupTimeoutMs?: number;
}

// ---- extension definition -----------------------------------------------------

export interface TachikomaExtension<C = unknown> {
  name: string;
  configSchema?: TSchema;
  setup(app: AppContext<C>): void | Promise<void>;
}

export const defineExtension = <C = unknown>(
  extension: TachikomaExtension<C>,
): TachikomaExtension<C> => extension;
