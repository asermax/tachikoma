import type { AgentSession, ExtensionFactory } from "@earendil-works/pi-coding-agent";

import { streamPrompt } from "./agent/adapter.ts";
import type { AgentManager } from "./agent/manager.ts";
import type { Channel, Delivery } from "./channels/types.ts";
import type { Config } from "./config/schema.ts";
import type { SessionRecord } from "./db/core-schema.ts";
import type { InboundMessage } from "./domain/message.ts";
import type { EventBus } from "./events.ts";
import type { ContextBlock, InboundContext, PostProcessingPhase } from "./extensions/api.ts";
import type { Registrations } from "./extensions/registrations.ts";
import type { Logger } from "./log.ts";
import type { SessionRegistry } from "./sessions/registry.ts";

interface ActiveSession {
  record: SessionRecord;
  session: AgentSession;
}

const PHASE_ORDER: PostProcessingPhase[] = ["main", "preFinalize", "finalize"];

export class Coordinator {
  private readonly inbox: InboundMessage[] = [];
  private wake: (() => void) | null = null;
  private active: ActiveSession | null = null;
  private pendingContext: ContextBlock[] = [];
  private readonly heldDeliveries: Delivery[] = [];
  private idleTimer: NodeJS.Timeout | null = null;
  private exchanging = false;
  private channel: Channel | null = null;

  private readonly config: Config;
  private readonly registry: SessionRegistry;
  private readonly agent: AgentManager;
  private readonly regs: Registrations;
  private readonly events: EventBus;
  private readonly log: Logger;

  constructor(
    config: Config,
    registry: SessionRegistry,
    agent: AgentManager,
    regs: Registrations,
    events: EventBus,
    log: Logger,
  ) {
    this.config = config;
    this.registry = registry;
    this.agent = agent;
    this.regs = regs;
    this.events = events;
    this.log = log;
  }

  // ---- channel wiring ---------------------------------------------------------

  attachChannel(channel: Channel): void {
    this.channel = channel;
  }

  submit(message: InboundMessage): void {
    this.inbox.push(message);
    this.wake?.();
    this.wake = null;
  }

  status(text: string): void {
    this.log.debug({ status: text }, "pipeline status");
    this.events.emit("status", { text });
  }

  deliver(delivery: Delivery): void {
    if (delivery.gate === "immediate" || (!this.exchanging && this.active == null)) {
      void this.sendDelivery(delivery);
      return;
    }

    this.heldDeliveries.push(delivery);

    if (delivery.maxHoldSeconds != null) {
      const timer = setTimeout(() => this.flushDeliveries(true), delivery.maxHoldSeconds * 1000);
      timer.unref();
    }
  }

  // ---- pi integration -----------------------------------------------------------

  /** Host-owned pi extension: injects gathered context blocks before each agent run. */
  hostFactory(): ExtensionFactory {
    return (pi) => {
      pi.on("before_agent_start", () => {
        if (this.pendingContext.length === 0) return undefined;

        const content = this.pendingContext
          .map((block) => `<context owner="${block.tag}">\n${block.content}\n</context>`)
          .join("\n\n");
        this.pendingContext = [];

        return {
          message: { customType: "tachikoma-context", content, display: false },
        };
      });
    };
  }

  // ---- main loop ----------------------------------------------------------------

  async run(signal: AbortSignal): Promise<void> {
    const onAbort = () => {
      this.wake?.();
      this.wake = null;
    };
    signal.addEventListener("abort", onAbort);

    try {
      while (!signal.aborted) {
        const message = this.inbox.shift();

        if (message == null) {
          await new Promise<void>((resolve) => {
            this.wake = resolve;
          });
          continue;
        }

        try {
          await this.handle(message);
        } catch (error) {
          this.log.error({ err: error }, "exchange failed");
        }
      }
    } finally {
      signal.removeEventListener("abort", onAbort);
      this.clearIdleTimer();
      // Held notices must not die with the process — push them out before closing.
      this.flushDeliveries(true);
      await this.closeActiveSession();
    }
  }

  current(): SessionRecord | null {
    return this.active?.record ?? null;
  }

  async closeActiveSession(): Promise<void> {
    const active = this.active;
    if (active == null) return;

    this.active = null;
    this.clearIdleTimer();
    active.session.dispose();

    const record = this.registry.close(active.record.id);
    this.log.info({ sessionId: record.id }, "session closed");
    this.events.emit("session:closed", { session: record });

    await this.runPostProcessing(record);
  }

  /** Close sessions left open by a previous run so their post-processing still happens. */
  async recoverDanglingSessions(): Promise<void> {
    for (const record of this.registry.findDangling()) {
      const closed = this.registry.close(record.id);
      this.log.info({ sessionId: closed.id }, "recovered dangling session from previous run");
      await this.runPostProcessing(closed);
    }
  }

  async resumeSession(record: SessionRecord): Promise<void> {
    await this.closeActiveSession();

    const reopened = this.registry.reopen(record.id);
    const session = await this.agent.open({ sessionFile: reopened.piSessionFile });

    this.active = { record: reopened, session };
    this.log.info({ sessionId: reopened.id }, "session resumed");
    this.events.emit("session:opened", { session: reopened, resumed: true });
  }

  // ---- internals ------------------------------------------------------------------

  private async handle(message: InboundMessage): Promise<void> {
    this.exchanging = true;

    try {
      await this.runInboundMiddleware(message);

      const active = await this.ensureSession(message.channel);
      const blocks = await this.collectContext(message, active.record);
      this.pendingContext.push(...blocks);

      const events = streamPrompt(active.session, renderPrompt(message));
      await this.channel?.respond({ message, events });

      await this.runExchangeProcessors(active, message);
      this.resetIdleTimer();
    } finally {
      this.exchanging = false;
      this.flushDeliveries(false);
    }
  }

  private async ensureSession(channel: string): Promise<ActiveSession> {
    if (this.active != null) return this.active;

    const session = await this.agent.open();
    const record = this.registry.create(channel, session.sessionFile ?? null);

    this.active = { record, session };
    this.log.info({ sessionId: record.id }, "session opened");
    this.events.emit("session:opened", { session: record, resumed: false });

    for (const hook of this.regs.sessionOpenHooks) {
      try {
        await hook(record);
      } catch (error) {
        this.log.error({ err: error }, "session open hook failed");
      }
    }

    return this.active;
  }

  private async runInboundMiddleware(message: InboundMessage): Promise<void> {
    const context: InboundContext = {
      session: this.active?.record ?? null,
      closeSession: () => this.closeActiveSession(),
      resumeSession: (record) => this.resumeSession(record),
    };

    const chain = [...this.regs.inboundMiddleware];

    const invoke = async (index: number): Promise<void> => {
      const middleware = chain[index];
      if (middleware == null) return;

      await middleware(message, context, () => invoke(index + 1));
    };

    await invoke(0);
  }

  private async collectContext(
    message: InboundMessage,
    session: SessionRecord,
  ): Promise<ContextBlock[]> {
    const results = await Promise.allSettled(
      this.regs.contextProviders.map(async (provider) => {
        this.status(`Gathering context: ${provider.name}…`);
        return provider.provide({ message, session });
      }),
    );

    const blocks: ContextBlock[] = [];

    results.forEach((result, index) => {
      if (result.status === "fulfilled") {
        if (result.value != null) blocks.push(result.value);
      } else {
        this.log.error(
          { provider: this.regs.contextProviders[index]?.name, err: result.reason },
          "context provider failed",
        );
      }
    });

    return blocks;
  }

  private async runExchangeProcessors(
    active: ActiveSession,
    message: InboundMessage,
  ): Promise<void> {
    const assistantText = lastAssistantText(active.session);

    const results = await Promise.allSettled(
      this.regs.exchangeProcessors.map((processor) =>
        processor.process({
          session: active.record,
          userText: message.text,
          assistantText,
        }),
      ),
    );

    results.forEach((result, index) => {
      if (result.status === "rejected") {
        this.log.error(
          { processor: this.regs.exchangeProcessors[index]?.name, err: result.reason },
          "exchange processor failed",
        );
      }
    });

    // Refresh the cached record — processors typically update summary/lastExchange.
    const refreshed = this.registry.get(active.record.id);
    if (refreshed != null && this.active?.record.id === refreshed.id) {
      this.active = { ...this.active, record: refreshed };
    }
  }

  private async runPostProcessing(record: SessionRecord): Promise<void> {
    const state: Record<string, "completed" | "failed"> = {
      ...(record.postProcessingState ?? {}),
    };

    for (const phase of PHASE_ORDER) {
      const processors = this.regs.postProcessors.filter(
        (processor) =>
          (processor.phase ?? "main") === phase && state[processor.name] !== "completed",
      );
      if (processors.length === 0) continue;

      const results = await Promise.allSettled(
        processors.map((processor) => {
          this.status(`Post-processing: ${processor.name}…`);
          return processor.process({
            session: record,
            transcriptPath: record.piSessionFile,
            log: this.log.child({ processor: processor.name }),
          });
        }),
      );

      results.forEach((result, index) => {
        const processor = processors[index];
        if (processor == null) return;

        state[processor.name] = result.status === "fulfilled" ? "completed" : "failed";

        if (result.status === "rejected") {
          this.log.error(
            { processor: processor.name, err: result.reason },
            "post-processor failed",
          );
        }
      });
    }

    this.registry.update(record.id, { postProcessingState: state });
    this.events.emit("session:post-processed", { sessionId: record.id, state });
  }

  private resetIdleTimer(): void {
    this.clearIdleTimer();

    this.idleTimer = setTimeout(() => {
      this.log.info("idle timeout reached — closing session");
      void this.closeActiveSession().catch((error) =>
        this.log.error({ err: error }, "idle close failed"),
      );
    }, this.config.sessions.idleCloseSeconds * 1000);
    this.idleTimer.unref();
  }

  private clearIdleTimer(): void {
    if (this.idleTimer != null) clearTimeout(this.idleTimer);
    this.idleTimer = null;
  }

  private flushDeliveries(force: boolean): void {
    if (!force && this.exchanging) return;

    const pending = this.heldDeliveries.splice(0);
    for (const delivery of pending) void this.sendDelivery(delivery);
  }

  private async sendDelivery(delivery: Delivery): Promise<void> {
    try {
      await this.channel?.deliver(delivery);
    } catch (error) {
      this.log.error({ err: error }, "delivery failed");
    }
  }
}

const renderPrompt = (message: InboundMessage): string => {
  if (message.media.length === 0) return message.text;

  const attachments = message.media
    .map(
      (item) =>
        `- ${item.kind} at ${item.path}${item.description != null ? ` — ${item.description}` : ""}`,
    )
    .join("\n");

  return `${message.text}\n\n<attachments>\n${attachments}\n</attachments>`;
};

const lastAssistantText = (session: AgentSession): string => {
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    const message = session.messages[index];
    if (message == null || message.role !== "assistant") continue;

    const content = (message as { content?: unknown }).content;
    if (!Array.isArray(content)) continue;

    return content
      .filter(
        (block): block is { type: "text"; text: string } =>
          typeof block === "object" &&
          block != null &&
          (block as { type?: string }).type === "text",
      )
      .map((block) => block.text)
      .join("");
  }

  return "";
};
