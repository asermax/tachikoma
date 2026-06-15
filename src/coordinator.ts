import { existsSync } from "node:fs";

import type { AgentSession, ExtensionFactory } from "@earendil-works/pi-coding-agent";

import { streamPrompt } from "./agent/adapter.ts";
import type { AgentManager } from "./agent/manager.ts";
import { lastAssistantText } from "./agent/side-run.ts";
import { buildDigest } from "./channels/delivery-digest.ts";
import { compareQueued, evaluate, type QueuedItem } from "./channels/delivery-queue.ts";
import type { Channel, Delivery } from "./channels/types.ts";
import type { SessionRecord } from "./db/core-schema.ts";
import { type AgentEvent, ERROR_KINDS } from "./domain/agent-events.ts";
import type { InboundMessage } from "./domain/message.ts";
import type { EventBus } from "./events.ts";
import type { InboundContext } from "./extensions/api.ts";
import { runPhasedPostProcessors } from "./extensions/post-processing.ts";
import type { Registrations } from "./extensions/registrations.ts";
import type { Logger } from "./log.ts";
import type { SessionRegistry } from "./sessions/registry.ts";

interface ActiveSession {
  record: SessionRecord;
  session: AgentSession;
}

export class Coordinator {
  private readonly inbox: InboundMessage[] = [];
  private wake: (() => void) | null = null;
  private active: ActiveSession | null = null;
  /** Bridging-context blocks injected once on the next agent run (session resume). */
  private pendingContext: string[] = [];
  private readonly heldDeliveries: QueuedItem[] = [];
  /** Timestamp of the most recently completed exchange; the queue's idle-window anchor. */
  private lastExchangeAt: Date | null = null;
  /** Single shared timer driving the next queue re-evaluation (never one-per-item). */
  private deliveryTimer: ReturnType<typeof setTimeout> | null = null;
  private exchanging = false;
  private shuttingDown = false;
  private channel: Channel | null = null;

  private readonly registry: SessionRegistry;
  private readonly agent: AgentManager;
  private readonly regs: Registrations;
  private readonly events: EventBus;
  private readonly log: Logger;
  private readonly now: () => Date;

  constructor(
    registry: SessionRegistry,
    agent: AgentManager,
    regs: Registrations,
    events: EventBus,
    log: Logger,
    now: () => Date = () => new Date(),
  ) {
    this.registry = registry;
    this.agent = agent;
    this.regs = regs;
    this.events = events;
    this.log = log;
    this.now = now;
  }

  // ---- channel wiring ---------------------------------------------------------

  attachChannel(channel: Channel): void {
    this.channel = channel;
  }

  submit(message: InboundMessage): void {
    // Leading slash commands are channel-agnostic. "/queue …" opts out of steering
    // (the message waits for the next exchange); "/new …" forces a fresh session,
    // honored downstream by the boundary extension. Both skip steering: neither
    // wants to fold into the live run.
    const queued = message.text.startsWith("/queue ");
    const forceNew = message.text.startsWith("/new ");

    const normalized =
      queued || forceNew
        ? {
            ...message,
            text: message.text.slice(message.text.indexOf(" ") + 1).trim(),
            metadata: {
              ...message.metadata,
              ...(queued ? { queued: true } : { forceNew: true }),
            },
          }
        : message;

    // Messages arriving mid-exchange steer the live run instead of waiting in line.
    if (
      !queued &&
      !forceNew &&
      this.exchanging &&
      this.active != null &&
      normalized.metadata.origin !== "system"
    ) {
      const session = this.active.session;

      void session
        .steer(renderPrompt(normalized))
        .catch((error) => this.log.error({ err: error }, "steering failed — message dropped"));
      return;
    }

    this.inbox.push(normalized);
    this.wake?.();
    this.wake = null;
  }

  /** Abort the in-flight agent run, if any (user-initiated stop). */
  async abortExchange(): Promise<void> {
    await this.active?.session.abort();
  }

  status(text: string): void {
    this.log.debug({ status: text }, "pipeline status");
    this.events.emit("status", { text });

    try {
      // During teardown there is no streaming renderer to host the line, so
      // route to the dedicated shutdown message instead (e.g. per-processor
      // "Post-processing: …" progress shown as part of the shutdown sequence).
      if (this.shuttingDown && this.channel?.shutdownStatus != null) {
        void this.channel.shutdownStatus(text);
      } else {
        this.channel?.status?.(text);
      }
    } catch (error) {
      this.log.debug({ err: error }, "channel status rendering failed");
    }
  }

  /**
   * Emit a shutdown-sequence line and await its delivery, so the message lands
   * before the process exits. Falls back to the fire-and-forget `status`
   * surface for channels without a dedicated shutdown message (e.g. the REPL).
   */
  private async emitShutdownStatus(text: string): Promise<void> {
    this.log.debug({ status: text }, "shutdown status");
    this.events.emit("status", { text });

    try {
      if (this.channel?.shutdownStatus != null) {
        await this.channel.shutdownStatus(text);
      } else {
        this.channel?.status?.(text);
      }
    } catch (error) {
      this.log.debug({ err: error }, "shutdown status rendering failed");
    }
  }

  deliver(delivery: Delivery): void {
    // Synchronous command UI (the /new ack) renders straight to the channel — never
    // queued. During shutdown even these hold so the final awaited drain orders them.
    if (delivery.immediate === true && !this.shuttingDown) {
      void this.channel?.deliver(delivery);
      return;
    }

    this.heldDeliveries.push({
      ...delivery,
      tier: delivery.tier ?? "normal",
      enqueuedAt: this.now().getTime(),
    });

    this.scheduleDelivery();
  }

  // ---- pi integration -----------------------------------------------------------

  /** Host-owned pi extension: injects gathered context blocks before each agent run. */
  hostFactory(): ExtensionFactory {
    return (pi) => {
      pi.on("before_agent_start", () => {
        if (this.pendingContext.length === 0) return undefined;

        const content = this.pendingContext.join("\n\n");
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

      // Set the flag before draining: shutdown hooks (e.g. the notifications router)
      // push their held output into heldDeliveries, which the flag keeps queued so the
      // single awaited drain below — not a racing agent turn — delivers them.
      this.shuttingDown = true;
      await this.runShutdownHooks();
      // No agent turn can run during teardown — render the remaining queue straight to
      // the channel as one digest so nothing dies with the process.
      await this.drainQueueToChannel();

      const announceShutdown = this.channel != null && this.active != null;

      if (announceShutdown) await this.emitShutdownStatus("Wrapping up the conversation…");

      // Per-processor progress emitted inside this call routes to the same
      // shutdown message (see status() — shuttingDown is set above).
      await this.closeActiveSession();

      if (announceShutdown) await this.emitShutdownStatus("Done");
    }
  }

  current(): SessionRecord | null {
    return this.active?.record ?? null;
  }

  /**
   * Close the active session only when no exchange is in flight. This is the
   * safe primitive for time-based policies (extensions cannot see the loop's
   * busy state); an in-flight exchange means the session is not idle anyway.
   */
  async closeActiveSessionIfIdle(): Promise<boolean> {
    if (this.exchanging) {
      this.log.debug("idle close skipped — exchange in flight");
      return false;
    }

    if (this.active == null) return false;

    await this.closeActiveSession();
    return true;
  }

  async closeActiveSession(): Promise<void> {
    const active = this.active;
    if (active == null) return;

    this.active = null;
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
    // A quarantined session must never be reopened — resuming it would rebuild on a corrupt
    // transcript. Keep the active session (mirrors the missing-file guard below).
    if (record.error) {
      this.log.warn({ sessionId: record.id }, "resume skipped — session is quarantined");
      return;
    }

    // Verify the target is openable BEFORE disposing the live session — a failed
    // open after teardown would leave the conversation with no active session.
    if (record.piSessionFile == null || !existsSync(record.piSessionFile)) {
      this.log.warn(
        { sessionId: record.id, sessionFile: record.piSessionFile },
        "resume skipped — pi session file missing; keeping the active session",
      );
      return;
    }

    const priorClosedAt = record.closedAt;

    await this.closeActiveSession();

    const reopened = this.registry.reopen(record.id);
    const session = await this.agent.open({ sessionFile: reopened.piSessionFile });

    this.active = { record: reopened, session };
    this.log.info({ sessionId: reopened.id }, "session resumed");
    this.events.emit("session:opened", { session: reopened, resumed: true });

    this.injectBridgingContext(priorClosedAt);
  }

  /**
   * Surface what happened while a resumed session was closed: concatenate the
   * summaries of sessions that closed between its prior close and now, oldest-first.
   */
  private injectBridgingContext(priorClosedAt: Date | null): void {
    if (priorClosedAt == null) return;

    const bridging = this.registry.listClosedBetween(priorClosedAt, new Date());
    if (bridging.length === 0) return;

    const content = bridging
      .map((session) => session.summary)
      .filter((summary): summary is string => summary != null)
      .join("\n\n");
    if (content.length === 0) return;

    this.pendingContext.push(content);
  }

  // ---- internals ------------------------------------------------------------------

  private async handle(message: InboundMessage): Promise<void> {
    this.exchanging = true;
    let encodingError = false;

    try {
      await this.runInboundMiddleware(message);

      // A middleware (e.g. the commands extension) fully handled the message.
      if (message.metadata.handled === true) return;

      const active = await this.ensureSession(message.channel);
      const wasErrored = active.record.error;

      // The channel consumes the stream; we observe it in flight to detect a terminal encoding
      // error (the adapter yields `error` only when `session.prompt()` rejects). An encoding
      // failure leaves the transcript/output un-encodable, so the session is quarantined.
      const events = tapEncodingErrors(streamPrompt(active.session, renderPrompt(message)), () => {
        encodingError = true;
      });
      await this.channel?.respond({ message, events });

      if (encodingError) {
        this.registry.markErrored(active.record.id);
        this.log.warn({ sessionId: active.record.id }, "session quarantined — encoding failure");
        const refreshed = this.registry.get(active.record.id);
        if (refreshed != null && this.active?.record.id === refreshed.id) {
          this.active = { ...this.active, record: refreshed };
        }
      }

      // A quarantined session's derived state (rolling summary, last exchange) is not maintained —
      // it will not be resumed or post-processed, so leave its record untouched rather than risk
      // summarizing the corrupt exchange.
      if (!wasErrored && !encodingError) {
        await this.runExchangeProcessors(active, message);
      }
    } finally {
      this.exchanging = false;
      this.lastExchangeAt = this.now();
      this.scheduleDelivery();
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

  private async runExchangeProcessors(
    active: ActiveSession,
    message: InboundMessage,
  ): Promise<void> {
    const assistantText = lastAssistantText(active.session.messages);

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
    // A quarantined session's transcript may be too corrupt to feed extractors/archivers — skip
    // the pipeline entirely rather than risk writing broken derived state (memories, archives).
    if (record.error) {
      this.log.warn({ sessionId: record.id }, "post-processing skipped — session quarantined");
      return;
    }

    const state: Record<string, "completed" | "failed"> = {
      ...(record.postProcessingState ?? {}),
    };

    await runPhasedPostProcessors({
      processors: this.regs.postProcessors,
      context: {
        session: record,
        transcriptPath: record.piSessionFile,
        log: this.log,
      },
      log: this.log,
      shouldSkip: (processor) => state[processor.name] === "completed",
      onProcessorStart: (processor) => this.status(`Post-processing: ${processor.name}…`),
      onProcessorSettled: (processor, result) => {
        state[processor.name] = result.status === "fulfilled" ? "completed" : "failed";
      },
    });

    this.registry.update(record.id, { postProcessingState: state });
    this.events.emit("session:post-processed", { sessionId: record.id, state });
  }

  /**
   * The sole queue decision point. Re-evaluates the held queue and either flushes it as
   * one agent turn or arms the shared timer for the next actionable moment. Called on
   * enqueue, on exchange completion, and from the timer itself — never recurses into a
   * flush directly, so re-evaluation is idempotent and never busy-spins.
   */
  private scheduleDelivery(): void {
    if (this.deliveryTimer != null) {
      clearTimeout(this.deliveryTimer);
      this.deliveryTimer = null;
    }

    if (this.shuttingDown || this.exchanging || this.heldDeliveries.length === 0) return;

    const result = evaluate(
      this.now().getTime(),
      this.lastExchangeAt?.getTime() ?? null,
      this.heldDeliveries,
    );
    if (result == null) return;

    if ("drain" in result) {
      this.flushQueue();
      return;
    }

    this.deliveryTimer = setTimeout(
      () => this.scheduleDelivery(),
      Math.max(0, result.wakeAt - this.now().getTime()),
    );
    this.deliveryTimer.unref();
  }

  /**
   * Drain the whole queue into one system-origin turn. `submit()` skips steering for
   * system-origin messages and wakes the parked loop, so the digest is delivered as a
   * fresh turn, never folded into an in-flight exchange.
   */
  private flushQueue(): void {
    const items = this.heldDeliveries.splice(0);
    if (items.length === 0) return;

    this.submit({
      text: buildDigest(items),
      channel: this.channel?.name ?? "system",
      receivedAt: this.now(),
      media: [],
      metadata: { origin: "system", boundary: "skip" },
    });
  }

  /** Shutdown-only: render the remaining queue to the channel as one digest. */
  private async drainQueueToChannel(): Promise<void> {
    if (this.deliveryTimer != null) {
      clearTimeout(this.deliveryTimer);
      this.deliveryTimer = null;
    }

    const items = this.heldDeliveries.splice(0).sort(compareQueued);
    if (items.length === 0) return;

    try {
      await this.channel?.deliver({ text: buildDigest(items) });
    } catch (error) {
      this.log.error({ err: error }, "shutdown delivery failed");
    }
  }

  private async runShutdownHooks(): Promise<void> {
    for (const { name, hook } of this.regs.shutdownHooks) {
      try {
        await hook();
      } catch (err) {
        this.log.error({ hook: name, err }, "shutdown hook failed");
      }
    }
  }
}

/**
 * Forward an exchange's event stream unchanged while watching for a terminal encoding error.
 * The adapter yields an `error` event only when `session.prompt()` rejects; an `encoding` kind
 * means the transcript/output is un-encodable, so the caller quarantines the session. This is a
 * single-consumer passthrough (not a tee): the channel still sees every event, the coordinator
 * just observes the error kind in flight.
 */
const tapEncodingErrors = (
  events: AsyncIterable<AgentEvent>,
  onEncoding: () => void,
): AsyncIterable<AgentEvent> =>
  (async function* () {
    for await (const event of events) {
      if (event.kind === "error" && event.errorKind === ERROR_KINDS.encoding) onEncoding();
      yield event;
    }
  })();

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
