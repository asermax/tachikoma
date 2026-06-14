import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import type { TSchema } from "typebox";
import type { ModelTier, ModelTiers } from "../agent/models.ts";
import type { SideRunner } from "../agent/side-run.ts";
import type { Channel, Delivery } from "../channels/types.ts";
import type { Config } from "../config/schema.ts";
import type { SessionRecord } from "../db/core-schema.ts";
import type { AppDatabase } from "../db/index.ts";
import type { KeyValueState } from "../db/state.ts";
import type { InboundMessage } from "../domain/message.ts";
import type { EventBus } from "../events.ts";
import type { CommitAllOptions } from "../git/commit.ts";
import type { PushResult, RebaseResolver, SyncResult } from "../git/sync.ts";
import type { Logger } from "../log.ts";
import type { Scheduler } from "../scheduler.ts";
import type { Workspace } from "../workspace.ts";

// ---- pipeline contracts -----------------------------------------------------

export interface ExchangeContext {
  session: SessionRecord;
  userText: string;
  assistantText: string;
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

export interface PostProcessorContext {
  /** Null for background runs that have no conversational session record. */
  session: SessionRecord | null;
  /** Path to the pi session JSONL transcript, when the session persisted one. */
  transcriptPath: string | null;
  log: Logger;
}

export interface PostProcessor {
  name: string;
  phase?: PostProcessingPhase;
  process(context: PostProcessorContext): Promise<void>;
}

export interface InboundContext {
  session: SessionRecord | null;
  /** Close the active session (post-processing runs) before the message is handled. */
  closeSession(): Promise<void>;
  /** Resume a previously closed session and make it active. */
  resumeSession(session: SessionRecord): Promise<void>;
}

export type InboundMiddleware = (
  message: InboundMessage,
  context: InboundContext,
  next: () => Promise<void>,
) => Promise<void>;

// ---- app services exposed to extensions --------------------------------------

export interface SessionsApi {
  current(): SessionRecord | null;
  get(id: number): SessionRecord | null;
  update(
    id: number,
    patch: Partial<Pick<SessionRecord, "summary" | "lastExchange">>,
  ): SessionRecord;
  listResumable(): SessionRecord[];
  /** Close the active session immediately — callers must know no exchange is streaming. */
  close(): Promise<void>;
  /** Close the active session only when no exchange is in flight; returns whether it closed. */
  closeIfIdle(): Promise<boolean>;
  /** Abort the in-flight agent run, if any (user-initiated stop). */
  abortExchange(): Promise<void>;
  onOpen(hook: (session: SessionRecord) => void | Promise<void>): void;
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
   * Contribute a pi extension factory, scoped to the given sessions. Context sections use this same
   * form via `provideContext(provide, customType?)` as the factory: with no `customType` the content
   * is appended to the system prompt, with a `customType` it is injected as a hidden message.
   */
  use(factory: ExtensionFactory, options?: UseFactoryOptions): void;
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
   * Stage everything in `cwd` and commit with a message — generated from the
   * staged diffstat when a `side` completer is supplied, or the explicit
   * `message`. Returns the commit message, or null when there was nothing to
   * commit. `log` defaults to the extension's logger when omitted.
   */
  commitAll(options: Omit<CommitAllOptions, "log"> & { log?: Logger }): Promise<string | null>;
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
