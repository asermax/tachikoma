import { existsSync } from "node:fs";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { streamPrompt } from "./agent/adapter.ts";
import type { AgentManager } from "./agent/manager.ts";
import { branchEntriesSinceBase } from "./agent/session-tree.ts";
import { buildDigest } from "./channels/delivery-digest.ts";
import { compareQueued, evaluate, type QueuedItem } from "./channels/delivery-queue.ts";
import type { Channel, Delivery } from "./channels/types.ts";
import type { InboundMessage } from "./domain/message.ts";
import type { EventBus } from "./events.ts";
import type { InboundContext, TrunkInbound } from "./extensions/api.ts";
import { runPhasedPostProcessors } from "./extensions/post-processing.ts";
import type { Registrations } from "./extensions/registrations.ts";
import type { Logger } from "./log.ts";
import {
  getBranchRecords,
  localDay,
  nextBranchId,
  openOrCreateTrunk,
  readBoomerangState,
  type TrunkState,
} from "./sessions/trunk.ts";

/** The live daily trunk the coordinator owns; identity lives in the `app_state` pointer, not a row. */
interface ActiveTrunk {
  session: AgentSession;
  day: string;
  sessionFile: string;
}

export class Coordinator {
  private readonly inbox: InboundMessage[] = [];
  private wake: (() => void) | null = null;
  private active: ActiveTrunk | null = null;
  /** True only while a trunk is open; the delivery gate holds drains until this flips. */
  private trunkLive = false;
  private readonly heldDeliveries: QueuedItem[] = [];
  /** Timestamp of the most recently completed exchange; the queue's idle-window anchor. */
  private lastExchangeAt: Date | null = null;
  /** Single shared timer driving the next queue re-evaluation (never one-per-item). */
  private deliveryTimer: ReturnType<typeof setTimeout> | null = null;
  private exchanging = false;
  private shuttingDown = false;
  private channel: Channel | null = null;

  private readonly trunkState: TrunkState;
  private readonly agent: AgentManager;
  private readonly regs: Registrations;
  private readonly events: EventBus;
  private readonly log: Logger;
  private readonly now: () => Date;
  private readonly timezone: string | undefined;

  constructor(
    trunkState: TrunkState,
    agent: AgentManager,
    regs: Registrations,
    events: EventBus,
    log: Logger,
    timezone: string | undefined,
    now: () => Date = () => new Date(),
  ) {
    this.trunkState = trunkState;
    this.agent = agent;
    this.regs = regs;
    this.events = events;
    this.log = log;
    this.timezone = timezone;
    this.now = now;
  }

  // ---- channel wiring ---------------------------------------------------------

  attachChannel(channel: Channel): void {
    this.channel = channel;
  }

  submit(message: InboundMessage): void {
    // Leading slash commands are channel-agnostic. "/queue …" opts out of steering
    // (the message waits for the next exchange); "/new …" forces a fresh topic branch,
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

  /** The live daily-trunk pi session, or null when no trunk is active. */
  activeTrunkSession(): AgentSession | null {
    return this.active?.session ?? null;
  }

  status(text: string, silent = false): void {
    this.log.debug({ status: text }, "pipeline status");
    this.events.emit("status", { text });

    // No renderer to host the line: stop here so it's logged for operators without
    // leaving a ghost status in the chat.
    if (silent) return;

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
   * surface for channels without a dedicated shutdown message.
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
          // A held background delivery can be the first event of the day: with no live trunk nothing
          // would ever drain it (the gate holds until trunkLive). Open today's trunk (after any
          // stale-day recovery) so the queue can flush, then re-evaluate.
          if (this.heldDeliveries.length > 0 && !this.trunkLive) {
            try {
              await this.ensureTrunk();
              this.scheduleDelivery();
            } catch (error) {
              this.log.error({ err: error }, "trunk open for held delivery failed");
            }
            continue;
          }

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
      await this.closeTrunk();

      if (announceShutdown) await this.emitShutdownStatus("Done");
    }
  }

  // ---- trunk lifecycle ----------------------------------------------------------

  /**
   * Ensure today's trunk is live. Before opening today's, if the active pointer belongs to an earlier
   * day, lazily close it first (the stale-day backstop for a missed nightly close — idempotent).
   * Fires the once-per-day open hooks when a trunk first becomes live.
   */
  private async ensureTrunk(): Promise<ActiveTrunk> {
    if (this.active != null) return this.active;

    const today = localDay(this.now, this.timezone);

    await this.closeStaleActivePointer(today);

    const opened = await openOrCreateTrunk(
      { agent: this.agent, trunk: this.trunkState, now: this.now, timezone: this.timezone },
      today,
    );

    this.active = {
      session: opened.session,
      day: opened.day,
      sessionFile: opened.sessionFile,
    };
    this.trunkLive = true;

    this.log.info({ sessionFile: opened.sessionFile, day: opened.day }, "trunk opened");
    this.events.emit("session:opened", { resumed: !opened.isNew });

    for (const hook of this.regs.sessionOpenHooks) {
      try {
        await hook();
      } catch (error) {
        this.log.error({ err: error }, "session open hook failed");
      }
    }

    return this.active;
  }

  /**
   * If the active pointer belongs to a day before `today`, close that trunk through the post-processing
   * pipeline before opening today's. The lazy stale-day backstop (ADR-014) for a close the nightly cron
   * missed; idempotent because the close pipeline's markers skip already-done work.
   */
  private async closeStaleActivePointer(today: string): Promise<void> {
    const pointer = this.trunkState.getActive();

    // Nothing to do for a missing or same-day pointer — the same-day pointer is reopened below.
    if (pointer == null || pointer.day >= today) return;

    // Stale but its file vanished: drop it from the index/pointer and move on.
    if (!existsSync(pointer.sessionFile)) {
      this.trunkState.retireTrunk(pointer.sessionFile);
      this.trunkState.clearActive();
      return;
    }

    const session = await this.agent.open({ sessionFile: pointer.sessionFile });

    await this.closeTrunkSession(
      { session, day: pointer.day, sessionFile: pointer.sessionFile },
      true,
    );
    this.trunkState.clearActive();
  }

  /**
   * Nightly-close trigger: close the live trunk only when one is active AND no exchange is in flight.
   * The coordinator loop is serial, so checking `exchanging` here is enough — a fired cron during a
   * live exchange is skipped (the lazy stale-day backstop in `ensureTrunk` closes it next day instead).
   */
  async closeTrunkIfDue(): Promise<void> {
    if (this.active == null || this.exchanging) {
      this.log.debug(
        { active: this.active != null, exchanging: this.exchanging },
        "nightly close skipped — no idle trunk",
      );
      return;
    }

    await this.closeTrunk();
  }

  /** Close the live trunk (shutdown / explicit). No-op when no trunk is open. */
  async closeTrunk(silent = false): Promise<void> {
    const active = this.active;
    if (active == null) return;

    this.active = null;
    this.trunkLive = false;

    await this.closeTrunkSession(active, silent);
    this.trunkState.clearActive();
  }

  /**
   * Run the close pipeline over a trunk: dispose the live session, post-process the trunk, then retire
   * it from the unclosed index. The retire happens ONLY after post-processing completes (the
   * write-ordering invariant's second half) so a crash mid-close keeps the trunk recoverable.
   */
  private async closeTrunkSession(trunk: ActiveTrunk, silent: boolean): Promise<void> {
    trunk.session.dispose();

    this.log.info({ sessionFile: trunk.sessionFile, day: trunk.day }, "trunk closed");
    this.events.emit("session:closed", { sessionFile: trunk.sessionFile, day: trunk.day });

    await this.runPostProcessing(trunk, silent);

    this.trunkState.retireTrunk(trunk.sessionFile);
  }

  /**
   * Close + post-process every stale-day trunk left by downtime, before the channel starts. Reads the
   * active pointer plus the `unclosed` index; any trunk whose day is before today (and whose file still
   * exists) is run through the idempotent close pipeline and retired. First run ever (no pointer, no
   * files) is a clean no-op.
   */
  async recoverStaleTrunks(): Promise<void> {
    const today = localDay(this.now, this.timezone);
    const pointer = this.trunkState.getActive();

    const files = new Set(this.trunkState.listUnclosed());
    const pointerDay = new Map<string, string>();

    if (pointer != null) {
      files.add(pointer.sessionFile);
      pointerDay.set(pointer.sessionFile, pointer.day);
    }

    for (const file of files) {
      if (!existsSync(file)) {
        this.trunkState.retireTrunk(file);
        continue;
      }

      const day = pointerDay.get(file) ?? today;

      // An unclosed trunk with no pointer-day is from a prior run by definition; treat the pointer's
      // own trunk as stale only when its day precedes today.
      if (pointerDay.has(file) && day >= today) continue;

      this.log.info({ sessionFile: file, day }, "recovering stale trunk");

      const session = await this.agent.open({ sessionFile: file });
      await this.closeTrunkSession({ session, day, sessionFile: file }, true);
    }

    if (pointer != null && pointer.day < today) this.trunkState.clearActive();
  }

  // ---- internals ------------------------------------------------------------------

  private async handle(message: InboundMessage): Promise<void> {
    this.exchanging = true;

    try {
      // Ensure the trunk BEFORE the inbound middleware so the boundary middleware can drive collapse on
      // the live trunk. A command middleware that sets `handled` still short-circuits before streaming.
      const active = await this.ensureTrunk();

      await this.runInboundMiddleware(message, active);

      // A middleware (e.g. the commands extension) fully handled the message.
      if (message.metadata.handled === true) return;

      const events = streamPrompt(active.session, renderPrompt(message));
      await this.channel?.respond({ message, events });

      await this.runExchangeProcessors(message);
    } finally {
      this.exchanging = false;
      this.lastExchangeAt = this.now();
      this.scheduleDelivery();
    }
  }

  private async runInboundMiddleware(message: InboundMessage, active: ActiveTrunk): Promise<void> {
    const context: InboundContext = { trunk: this.buildTrunkInbound(active) };

    const chain = [...this.regs.inboundMiddleware];

    const invoke = async (index: number): Promise<void> => {
      const middleware = chain[index];
      if (middleware == null) return;

      await middleware(message, context, () => invoke(index + 1));
    };

    await invoke(0);
  }

  /** Snapshot the live trunk for the inbound middleware (current base, branch records, empty-branch guard). */
  private buildTrunkInbound(active: ActiveTrunk): TrunkInbound {
    const branchRecords = getBranchRecords(active.session);
    const currentBaseId =
      readBoomerangState(active.session)?.currentTopicBaseId ??
      branchRecords.at(-1)?.summaryEntryId ??
      null;

    return {
      session: active.session,
      sessionFile: active.sessionFile,
      currentBaseId,
      branchRecords,
      liveBranchId: nextBranchId(branchRecords),
      hasAssistantTurnSinceBase: hasAssistantTurnSinceBase(active.session, currentBaseId),
    };
  }

  private async runExchangeProcessors(message: InboundMessage): Promise<void> {
    if (this.regs.exchangeProcessors.length === 0) return;

    const results = await Promise.allSettled(
      this.regs.exchangeProcessors.map((processor) =>
        processor.process({ userText: message.text }),
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
  }

  private async runPostProcessing(trunk: ActiveTrunk, silent = false): Promise<void> {
    await runPhasedPostProcessors({
      processors: this.regs.postProcessors,
      context: {
        trunk: {
          session: trunk.session,
          sessionFile: trunk.sessionFile,
          day: trunk.day,
          branchRecords: getBranchRecords(trunk.session),
        },
        transcriptPath: trunk.sessionFile,
        log: this.log,
      },
      log: this.log,
      onProcessorStart: (processor) => this.status(`Post-processing: ${processor.name}…`, silent),
    });

    this.events.emit("session:post-processed", { sessionFile: trunk.sessionFile });
  }

  /**
   * The sole queue decision point. Re-evaluates the held queue and either flushes it as
   * one agent turn or arms the shared timer for the next actionable moment. Called on
   * enqueue, on exchange completion, and from the timer itself — never recurses into a
   * flush directly, so re-evaluation is idempotent and never busy-spins. Nothing drains
   * while an exchange is in flight OR before a trunk is live (deliveries land in a live trunk).
   */
  private scheduleDelivery(): void {
    if (this.deliveryTimer != null) {
      clearTimeout(this.deliveryTimer);
      this.deliveryTimer = null;
    }

    if (this.shuttingDown || this.exchanging || this.heldDeliveries.length === 0) return;

    // A held delivery with no live trunk: wake the parked loop so it opens today's trunk (the
    // first-event-of-the-day path) and re-enters this method with trunkLive set.
    if (!this.trunkLive) {
      this.wake?.();
      this.wake = null;
      return;
    }

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

/** Whether the live branch (entries after `baseId` on the leaf path) holds an assistant message. */
const hasAssistantTurnSinceBase = (session: AgentSession, baseId: string | null): boolean =>
  branchEntriesSinceBase(session, baseId).some(
    (entry) => entry.type === "message" && entry.message.role === "assistant",
  );

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
