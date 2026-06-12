import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import type { TSchema } from "typebox";
import type { ModelTiers } from "../agent/models.ts";
import type { SideRunner } from "../agent/side-run.ts";
import type { Channel, Delivery } from "../channels/types.ts";
import type { Config } from "../config/schema.ts";
import type { SessionRecord } from "../db/core-schema.ts";
import type { AppDatabase } from "../db/index.ts";
import type { KeyValueState } from "../db/state.ts";
import type { InboundMessage } from "../domain/message.ts";
import type { EventBus } from "../events.ts";
import type { Logger } from "../log.ts";
import type { Scheduler } from "../scheduler.ts";
import type { Workspace } from "../workspace.ts";

// ---- pipeline contracts -----------------------------------------------------

export interface ContextBlock {
  /** Tag identifying the owner section, e.g. "memories", "projects". */
  tag: string;
  content: string;
}

export interface ContextProviderInput {
  message: InboundMessage;
  session: SessionRecord;
}

export interface ContextProvider {
  name: string;
  provide(input: ContextProviderInput): Promise<ContextBlock | null>;
}

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
  session: SessionRecord;
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
  close(): Promise<void>;
  onOpen(hook: (session: SessionRecord) => void | Promise<void>): void;
  onExchange(processor: ExchangeProcessor): void;
  registerProcessor(processor: PostProcessor): void;
}

export interface ChannelsApi {
  register(channel: Channel): void;
  deliver(delivery: Delivery): void;
}

export interface AgentApi {
  /** Contribute a pi extension factory to every agent session the host creates. */
  use(factory: ExtensionFactory): void;
  /** Contribute a section to the agent's system prompt (replaces pi's coding prompt). */
  systemPrompt(builder: () => string): void;
  provideContext(provider: ContextProvider): void;
  readonly models: ModelTiers;
  /** Side-channel LLM work: headless runs and structured classification. */
  readonly side: SideRunner;
}

export interface InboundApi {
  use(middleware: InboundMiddleware): void;
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

  bootstrap(name: string, hook: () => void | Promise<void>): void;
  /** Surface a progress line through the active channel while processing. */
  status(text: string): void;
  /** Enqueue another extension for loading (external extension support). */
  registerExtension(extension: TachikomaExtension<never>): void;
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
