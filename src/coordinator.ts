import { existsSync } from "node:fs";

import type { AgentSession } from "@earendil-works/pi-coding-agent";

import { streamPrompt } from "./agent/adapter.ts";
import { collapseLiveTopicBranch } from "./agent/branch-collapse.ts";
import type { AgentManager } from "./agent/manager.ts";
import { branchEntriesSinceBase, sessionCreatedAt } from "./agent/session-tree.ts";
import { SideRunner } from "./agent/side-run.ts";
import { buildDigest } from "./channels/delivery-digest.ts";
import { compareQueued, evaluate, type QueuedItem } from "./channels/delivery-queue.ts";
import type { Channel, Delivery } from "./channels/types.ts";
import { type DecisionHeader, decisionHeaderFrom, type InboundMessage } from "./domain/message.ts";
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

/**
 * How a trunk-close surfaces its post-processing progress.
 * - `transient` — the reclaimable preparation lead-in (an exchange follows to reclaim it).
 * - `lifecycle` — a dedicated, persistent message that survives into the next exchange, for closes
 *   with no following exchange (nightly cron, stale-trunk recovery) where a lead-in would orphan.
 */
type TrunkCloseVisibility = "transient" | "lifecycle";

export class Coordinator {
  private readonly inbox: InboundMessage[] = [];
  private wake: (() => void) | null = null;
  private active: ActiveTrunk | null = null;
  /** True only while a trunk is open; the delivery gate holds drains until this flips. */
  private trunkLive = false;
  /**
   * Non-null only while a `closeTrunk` close (the pipeline plus its trailing `clearActive`) is in
   * flight. Awaited by `ensureTrunk` to serialize close-before-open, so a message arriving mid-close
   * holds until the close settles instead of racing into a second close on the same trunk. NOT set by
   * `closeStaleActivePointer` (driven by `ensureTrunk` itself — advertising there would self-deadlock)
   * nor `recoverStaleTrunks` (startup-only, before the run loop). In-memory and unpersisted, so a
   * crash drops it and recovery never observes an in-flight close.
   */
  private closeInFlight: Promise<void> | null = null;
  private readonly heldDeliveries: QueuedItem[] = [];
  /** Timestamp of the most recently completed exchange; the queue's idle-window anchor. */
  private lastExchangeAt: Date | null = null;
  /** Single shared timer driving the next queue re-evaluation (never one-per-item). */
  private deliveryTimer: ReturnType<typeof setTimeout> | null = null;
  private exchanging = false;
  /**
   * A deferred process-restart thunk stashed by `requestRestart` (the `restart_self` /
   * `upgrade_self` tools). Set mid-exchange; checked after each exchange so the run loop
   * exits only once the full exchange lifecycle (response stream, exchange processors,
   * delivery re-eval) has completed. The graceful-drain `finally` then runs, and `app.ts`
   * performs the actual re-exec via `consumeRestartRequest` after channel/scheduler teardown.
   */
  private pendingRestart: (() => never) | null = null;
  /**
   * InboundMessages steered into the live run this exchange, awaiting consumption
   * confirmation. `submit()` pushes here when it steers; `handle()`'s finally reconciles
   * them against pi's pending-steering queue and rescues any the run orphaned. Cleared
   * every run-end.
   */
  private readonly steered: InboundMessage[] = [];
  private shuttingDown = false;
  private channel: Channel | null = null;
  /** True while a lifecycle trunk-close pipeline runs — routes `status()` to the lifecycle message. */
  private lifecycleActive = false;
  /** Set at the start of each lifecycle close so the first status line opens a fresh message. */
  private lifecycleFresh = false;
  /**
   * Lifecycle status buffered before the channel attached (stale-trunk recovery runs at startup,
   * before `attachChannel`). Flushed in order by `attachChannel` so each recovered trunk surfaces.
   */
  private lifecycleBuffer: { text: string; fresh: boolean }[] = [];

  private readonly trunkState: TrunkState;
  private readonly agent: AgentManager;
  /**
   * Side-channel LLM runner for trunk-close work that runs off the main conversation — currently the
   * live-branch summary generated when finalizing the day's final topic before extraction. Constructed
   * from `agent`/`log` exactly as each extension's `app.agent.side` is (see `host.ts`).
   */
  private readonly side: SideRunner;
  private readonly regs: Registrations;
  private readonly events: EventBus;
  private readonly log: Logger;
  private readonly now: () => Date;
  private readonly timezone: string | undefined;
  /** Milliseconds a bare arg-command waits for its argument before the pending prompt expires (R9). */
  private readonly pendingInputTtlMs: number;
  /**
   * Per-chat pending-input state (R9): keyed by channel (single-user bot). In-memory and ephemeral —
   * a restart drops it by design, so a stale prompt can never capture a post-restart message.
   */
  private readonly pendingInput = new Map<string, PendingInput>();

  constructor(
    trunkState: TrunkState,
    agent: AgentManager,
    regs: Registrations,
    events: EventBus,
    log: Logger,
    timezone: string | undefined,
    now: () => Date = () => new Date(),
    pendingInputTtlMs: number = DEFAULT_PENDING_INPUT_TTL_MS,
  ) {
    this.trunkState = trunkState;
    this.agent = agent;
    this.side = new SideRunner(agent, log);
    this.regs = regs;
    this.events = events;
    this.log = log;
    this.timezone = timezone;
    this.now = now;
    this.pendingInputTtlMs = pendingInputTtlMs;
  }

  // ---- channel wiring ---------------------------------------------------------

  attachChannel(channel: Channel): void {
    this.channel = channel;
    // Stale-trunk recovery runs before the channel attaches, so its lifecycle status was buffered.
    // Flush it now, in order, so each recovered trunk's close surfaces on its own message.
    void this.flushLifecycleBuffer();
  }

  submit(message: InboundMessage): void {
    // Pending-input gate (R9): MUST precede prefix-stripping and steering. submit() strips "/new "
    // and "/queue " prefixes and steers mid-exchange messages before any middleware runs, so a captured
    // argument would otherwise be mis-parsed as a command or steered into the live run. Returns true
    // when the message was consumed (a bare arg-command set a pending prompt, or a pending argument was
    // captured) — in which case submit() stops here. System-origin messages never participate, and
    // replay() bypasses submit() entirely (so a replayed turn is never captured as an argument).
    if (this.interceptPendingInput(message)) return;

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

    // Slash commands never steer: they belong to the inbound middleware and must queue to run through
    // that chain (a command fed to the live run — e.g. /rollback right after /stop aborts it — lands
    // as literal text, never executed). isCommand reads the original text, so /new and /queue —
    // normalized to plain text above — are still caught.
    const isCommand = message.text.trim().startsWith("/");

    if (
      !isCommand &&
      this.exchanging &&
      this.active != null &&
      normalized.metadata.origin !== "system"
    ) {
      const session = this.active.session;

      this.log.debug(
        { origin: normalized.metadata.origin, channel: normalized.channel },
        "message steered into live run",
      );

      // Track the steered message so handle()'s finally can rescue it if the run ends
      // without consuming it. steer() resolves at injection time and never rejects on an
      // ended run, so the only reliable orphan signal is pi's pending-steering queue at
      // run-end. A steer() rejection means the message never reached the run — rescue it
      // directly here (R3.1); a rejected steer is never in pi's queue, so the run-end
      // rescue classifies it as consumed and never double-rescues it.
      this.steered.push(normalized);
      void session.steer(renderPrompt(normalized)).catch((error) => {
        this.log.warn({ err: error }, "steering rejected — enqueuing for the next turn");
        this.enqueue(normalized);
      });
      return;
    }

    this.log.debug(
      { origin: normalized.metadata.origin, channel: normalized.channel, queued, forceNew },
      "message enqueued",
    );

    this.enqueue(normalized);
  }

  /**
   * Push `message` onto the inbox as the next exchange and wake the parked loop. Shared by
   * `submit()`'s normal enqueue path and the orphan-steer rescue so both land the same way.
   */
  private enqueue(message: InboundMessage): void {
    this.inbox.push(message);
    this.wake?.();
    this.wake = null;
  }

  // ---- pending-input (R9) -------------------------------------------------------

  /**
   * Pending-input gate, called at the very top of {@link submit}. Returns true when the message was
   * consumed by the pending-input flow and submit() should stop; false when it should proceed to
   * prefix-stripping/steering/enqueue as usual.
   *
   * - Bare arg-command ("/new", "/queue", "/skill" with no argument): sets a per-chat pending state and
   *   renders a hardcoded (non-LLM) prompt via the channel's status surface; the message is not enqueued.
   * - While a pending state is active: a slash command cancels it and is processed normally; any other
   *   message is captured as the argument and re-dispatched as "<command> <arg>".
   *
   * System-origin messages (queue digests) never participate — they are not user replies. `replay()`
   * routes around submit() entirely, so a replayed triggering message is never captured as an argument.
   */
  private interceptPendingInput(message: InboundMessage): boolean {
    if (message.metadata.origin === "system") return false;

    const chatKey = message.channel;
    this.expirePendingInput(chatKey);

    const trimmed = message.text.trim();

    // While a pending state is active, a non-command message is captured as the argument. Any slash
    // command cancels it and falls through to bare-command detection below — so a different bare
    // arg-command (e.g. /queue during a pending /new) re-enters pending-input for itself, while a
    // non-arg command (e.g. /checkpoint) proceeds to be enqueued normally.
    const pending = this.pendingInput.get(chatKey);
    if (pending != null && !trimmed.startsWith("/")) {
      // Capture the text as the argument, clear pending (so the re-dispatch isn't itself intercepted),
      // and re-dispatch as "<command> <arg>". A whitespace-only argument just clears the pending state.
      this.clearPendingInput(chatKey);
      if (trimmed.length > 0) {
        this.submit({ ...message, text: `/${pending.command} ${trimmed}` });
      }
      return true;
    }
    if (pending != null) {
      // A command arrived: cancel the pending flow, then fall through to (re)detect a bare command.
      this.clearPendingInput(chatKey);
    }

    // (Re)detect a bare arg-taking command: either with no prior pending, or a command that just
    // cancelled one. Sets pending + renders the prompt instead of enqueuing the bare command.
    const command = bareArgCommand(trimmed);
    if (command != null) {
      this.setPendingInput(chatKey, command);
      return true;
    }

    return false;
  }

  /** Set a pending state for `chatKey` and render its non-LLM prompt as its own dedicated message. */
  private setPendingInput(chatKey: string, command: PendingCommand): void {
    this.clearPendingInput(chatKey); // replace any prior pending (clears its timer)

    const promptedAt = this.now().getTime();
    const timer = setTimeout(() => {
      this.log.debug({ chatKey, command }, "pending-input expired (TTL)");
      this.clearPendingInput(chatKey);
    }, this.pendingInputTtlMs);
    timer.unref?.(); // never keep the process alive solely to expire a prompt

    this.pendingInput.set(chatKey, { command, promptedAt, timer });

    // Render as its own immediate message via `deliver()` (R14): unlike `status()`, it is never
    // appended to an in-progress streaming response nor reclaimed as a lead-in by a later one.
    this.deliver({ text: PENDING_PROMPTS[command], immediate: true });
  }

  /** Drop the pending state for `chatKey` (and its TTL timer), if any. */
  private clearPendingInput(chatKey: string): void {
    const pending = this.pendingInput.get(chatKey);
    if (pending == null) return;
    clearTimeout(pending.timer);
    this.pendingInput.delete(chatKey);
  }

  /** Defensive stale-check: drop an expired entry even if its timer hasn't fired yet. */
  private expirePendingInput(chatKey: string): void {
    const pending = this.pendingInput.get(chatKey);
    if (pending != null && this.now().getTime() - pending.promptedAt > this.pendingInputTtlMs) {
      this.clearPendingInput(chatKey);
    }
  }

  /** Abort the in-flight agent run, if any (user-initiated stop). */
  async abortExchange(): Promise<void> {
    this.log.info("exchange aborted by user");

    await this.active?.session.abort();
  }

  /**
   * Request a deferred process restart (`restart_self` / `upgrade_self`). The re-exec does
   * NOT happen immediately: the current exchange runs to completion, the graceful-drain
   * `finally` runs, and `app.ts` performs the re-exec via {@link consumeRestartRequest}
   * after channel/scheduler teardown. First-write-wins — every restarter thunk re-execs the
   * same on-disk entry, so concurrent requests are harmless.
   */
  requestRestart(restart: () => never): void {
    if (this.pendingRestart == null) this.pendingRestart = restart;
  }

  /**
   * Return and clear the deferred-restart thunk, if any. Called by `app.ts` after the run
   * loop and channel/scheduler teardown, so the re-exec is the last thing the process does.
   */
  consumeRestartRequest(): (() => never) | null {
    const restart = this.pendingRestart;
    this.pendingRestart = null;
    return restart;
  }

  /**
   * Re-run `text` as a fresh system-origin turn (DLT-181 rollback replay). The boundary extension
   * performs the tree surgery, then hands the triggering message here to be re-answered under the
   * corrected framing. This bypasses `submit()` entirely — no `/queue`/`/new` prefix-stripping, no
   * mid-exchange steering, no pending-input capture (Batch 5) — so the replayed text runs verbatim.
   * `boundary: "skip"` keeps the boundary classifier from re-classifying the turn (the framing is
   * already applied); `origin: "system"` matches the queue-flush shape so it is never itself steered.
   *
   * Routes through the `handle()` turn path by enqueueing the synthetic message at the front of the
   * inbox: the coordinator loop (serial) picks it up as the next exchange once the rollback command's
   * own (handled) exchange unwinds — a fresh, non-reentrant turn that streams a full response carrying
   * `header` (the "🔄 Rolled back…" descriptor, forwarded turn-scoped like any decision header).
   */
  replay(text: string, header?: DecisionHeader): void {
    this.inbox.unshift({
      text,
      channel: this.channel?.name ?? "system",
      receivedAt: this.now(),
      media: [],
      metadata: {
        origin: "system",
        boundary: "skip",
        // Mark replays so the boundary middleware's system-origin checkpoint rule (issue-411) excludes
        // them: a replay's framing is already applied (a checkpoint in rollback Case B, a topic in Case
        // A), so it must not be re-checkpointed.
        replay: true,
        ...(header != null ? { decisionHeader: header } : {}),
      },
    });

    this.wake?.();
    this.wake = null;
  }

  /** The live daily-trunk pi session, or null when no trunk is active. */
  activeTrunkSession(): AgentSession | null {
    return this.active?.session ?? null;
  }

  status(text: string): void {
    this.log.debug({ status: text }, "pipeline status");
    this.events.emit("status", { text });

    try {
      // A lifecycle trunk-close is running: surface the line on the dedicated, persistent lifecycle
      // message instead of the reclaimable lead-in — there is no following exchange to reclaim it.
      // The first line opens a fresh message (one per close); the rest edit it in place.
      if (this.lifecycleActive) {
        const fresh = this.lifecycleFresh;
        this.lifecycleFresh = false;
        void this.lifecycleStatus(text, fresh);
      } else if (this.shuttingDown && this.channel?.shutdownStatus != null) {
        // During teardown there is no streaming renderer to host the line, so route to the dedicated
        // shutdown message instead (e.g. per-processor "Post-processing: …" progress shown as part
        // of the shutdown sequence).
        void this.channel.shutdownStatus(text);
      } else {
        this.channel?.status?.(text);
      }
    } catch (error) {
      this.log.warn({ err: error }, "channel status rendering failed");
    }
  }

  /**
   * Render a trunk-lifecycle line on the dedicated, persistent lifecycle message (one per close).
   * While no channel is attached yet (stale-trunk recovery at startup), the line is buffered and
   * flushed in order by `attachChannel`. Channels without a `lifecycleStatus` surface fall back to
   * the reclaimable `status` line (or no-op).
   */
  private async lifecycleStatus(text: string, fresh = false): Promise<void> {
    if (this.channel == null) {
      this.lifecycleBuffer.push({ text, fresh });
      return;
    }

    try {
      if (this.channel.lifecycleStatus != null) {
        await this.channel.lifecycleStatus(text, fresh);
      } else {
        this.channel.status?.(text);
      }
    } catch (error) {
      this.log.debug({ err: error }, "lifecycle status rendering failed");
    }
  }

  /**
   * Replay lifecycle status buffered before the channel attached (stale-trunk recovery), in order.
   * Called once from `attachChannel`, which sets `this.channel` first — so by the time this runs the
   * buffer is frozen (no code path buffers post-attach: `lifecycleStatus` only buffers while the
   * channel is null), and the replay owns the buffer exclusively.
   */
  private async flushLifecycleBuffer(): Promise<void> {
    const buffered = this.lifecycleBuffer;
    this.lifecycleBuffer = [];
    for (const { text, fresh } of buffered) {
      await this.lifecycleStatus(text, fresh);
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

        // A deferred restart was requested during the exchange (restart_self / upgrade_self).
        // Break out so the graceful-drain finally runs and app.ts performs the re-exec — but only
        // after the full exchange lifecycle (response, processors, delivery re-eval) completed
        // above. This check precedes the while-condition's abort check, so a pending restart
        // always wins over a concurrent shutdown signal (the restart subsumes the shutdown).
        if (this.pendingRestart != null) break;
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
      const restarting = this.pendingRestart != null;

      if (announceShutdown) {
        await this.emitShutdownStatus(
          restarting ? "Restarting Tachikoma…" : "Wrapping up the conversation…",
        );
      }

      // The trunk deliberately survives shutdown — closing it here races a restarting process
      // (which reopens a fresh trunk) and redundantly re-pipelines a same-day restart; it is
      // closed by the nightly closeTrunkIfDue cron or recovered idempotently at next startup (ADR-014).
      if (announceShutdown) {
        await this.emitShutdownStatus(restarting ? "Restarting now…" : "Done");
      }
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

    // A `closeTrunk` close is in flight (nightly cron / `app.sessions.close()`): wait for it to
    // fully settle (pipeline + trailing `clearActive`) before resolving today's trunk. Otherwise this
    // would race ahead, find the closing trunk's stale-day pointer still set, and start a SECOND close
    // pipeline on the same session file via `closeStaleActivePointer` — duplicating branch extraction
    // and letting the late close's `clearActive()` wipe the newer trunk's pointer. A rejecting close
    // is caught so `closeStaleActivePointer` retries it idempotently rather than propagating the
    // failure into this exchange.
    if (this.closeInFlight != null) {
      try {
        await this.closeInFlight;
      } catch (error) {
        this.log.warn(
          { err: error },
          "in-flight trunk close rejected — retrying via lazy backstop",
        );
      }
    }

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

    // This runs before respond() (preparation phase), so the close progress surfaces on the lead-in
    // the upcoming response reclaims — no ghost line. Transient visibility lets per-processor lines
    // edit it live.
    this.status("Closing yesterday's trunk…");

    await this.closeTrunkSession(
      { session, day: pointer.day, sessionFile: pointer.sessionFile },
      "transient",
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

    // No exchange follows, so surface the close on a dedicated lifecycle message that persists
    // (rather than the reclaimable lead-in, which would orphan a "Post-processing: …" ghost line).
    await this.closeTrunk("lifecycle");
  }

  /**
   * Close the live trunk (nightly cron via closeTrunkIfDue, or explicit). No-op when no trunk is open.
   *
   * Runs under `closeInFlight` so a concurrent `ensureTrunk` (a message arriving mid-close) awaits the
   * whole close instead of racing into a second one. `active` is nulled before the field is set — no
   * `await` between — so a concurrent `ensureTrunk` sees no trunk and falls through to the in-flight
   * guard rather than returning the trunk being closed. See docs/feature-designs/conversation-loop.md
   * ("Close-in-flight hold") for the race this prevents.
   */
  async closeTrunk(visibility: TrunkCloseVisibility = "transient"): Promise<void> {
    const active = this.active;
    if (active == null) return;

    this.active = null;
    this.trunkLive = false;

    const run = async (): Promise<void> => {
      await this.closeTrunkSession(active, visibility);
      // Unconditional: by the time we land here, the closeInFlight hold guarantees no concurrent
      // ensureTrunk has opened a newer trunk (see the guard in ensureTrunk), so this clears exactly
      // the trunk we just closed.
      this.trunkState.clearActive();
    };
    const promise = run();
    this.closeInFlight = promise; // assigned before run()'s first await yields
    try {
      await promise;
    } finally {
      this.closeInFlight = null;
    }
  }

  /**
   * Run the close pipeline over a trunk: finalize the live branch (so the extraction pipeline picks it
   * up), dispose the live session, post-process the trunk, then retire it from the unclosed index. The
   * retire happens ONLY after post-processing completes (the write-ordering invariant's second half) so
   * a crash mid-close keeps the trunk recoverable.
   *
   * The lifecycle status flags wrap the whole body (cleared in `finally` so a throw can't leave
   * `status()` misrouted): the live-branch collapse emits a status line too, and on a lifecycle close it
   * must land on the dedicated lifecycle message rather than the reclaimable lead-in.
   */
  private async closeTrunkSession(
    trunk: ActiveTrunk,
    visibility: TrunkCloseVisibility,
  ): Promise<void> {
    if (visibility === "lifecycle") {
      this.lifecycleActive = true;
      this.lifecycleFresh = true;
    }

    let failures = 0;
    try {
      // Finalize the live branch BEFORE disposing the session (the collapse runs on the live session,
      // mirroring the topic-shift path) and before `runPostProcessing` snapshots the branch records — so
      // the just-collapsed branch is included in the extraction set. A failed collapse aborts the close
      // (failures = 1) so the trunk stays unclosed and retries; the collapse is idempotent, so a retry
      // that already collapsed the branch skips it (see `collapseLiveBranchForClose`).
      const collapseOk = await this.collapseLiveBranchForClose(trunk);

      trunk.session.dispose();

      this.log.info({ sessionFile: trunk.sessionFile, day: trunk.day }, "trunk closed");
      this.events.emit("session:closed", { sessionFile: trunk.sessionFile, day: trunk.day });

      if (collapseOk) {
        failures = await this.runPostProcessing(trunk);
      } else {
        failures = 1;
        this.log.warn(
          { sessionFile: trunk.sessionFile, day: trunk.day },
          "live-branch collapse failed — leaving trunk unclosed for retry",
        );
      }
    } finally {
      if (visibility === "lifecycle") {
        this.lifecycleActive = false;
        this.lifecycleFresh = false;
      }
    }

    if (visibility === "lifecycle") {
      // Final state: a failed close leaves the trunk unclosed for the next recovery to retry.
      await this.lifecycleStatus(failures > 0 ? "Trunk close failed" : "Trunk closed", false);
    }

    // Retire only on a fully clean close. A post-processor failure (e.g. partial memory extraction)
    // keeps the trunk in the unclosed index so the next recovery re-runs the pipeline; the per-phase
    // and per-branch markers make that re-run skip the work that already completed.
    if (failures > 0) {
      this.log.warn(
        { sessionFile: trunk.sessionFile, day: trunk.day, failures },
        "trunk post-processing had failures — leaving trunk unclosed for retry",
      );
      return;
    }

    this.trunkState.retireTrunk(trunk.sessionFile);
  }

  /**
   * Collapse the trunk's live branch as a topic branch so the close pipeline extracts it. Reuses the
   * topic-shift collapse (LLM summary + `branchWithSummary`); skipped when the live branch has no
   * assistant turn yet — the same empty-branch guard a topic shift uses, which is also what makes this
   * idempotent (after a collapse the leaf is re-seated onto the new summary, so the guard reads false on
   * a retry). Returns false only when the collapse itself failed, so the caller can abort the close and
   * retry rather than retire a trunk whose final conversation was never extracted.
   */
  private async collapseLiveBranchForClose(trunk: ActiveTrunk): Promise<boolean> {
    const { currentBaseId, liveBranchId, hasAssistantTurnSinceBase } = this.trunkBranchState(trunk);

    if (!hasAssistantTurnSinceBase) {
      // Empty (or already-collapsed) live branch — nothing to finalize. Treat as success so the close
      // proceeds; this is also the idempotent skip on a retry after a prior collapse.
      return true;
    }

    this.status("Closing the current topic…");

    const result = await collapseLiveTopicBranch(
      { side: this.side, log: this.log },
      {
        session: trunk.session,
        currentBaseId,
        branchId: liveBranchId,
        reason: "trunk close",
      },
    );

    return result != null;
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

      // The active pointer's own trunk is stale only when its day precedes today; a same-day
      // pointer is left for the coordinator to reopen.
      const pointerOwnDay = pointerDay.get(file);
      if (pointerOwnDay != null && pointerOwnDay >= today) continue;

      const session = await this.agent.open({ sessionFile: file });

      // The trunk's TRUE day: the pointer's day if known, else the session's own creation day — never
      // `today`. A trunk closed late (downtime, multi-day recovery) must keep the day it happened, so
      // its memories file under that date rather than the day we happened to recover it.
      const created = sessionCreatedAt(session);
      const day =
        pointerOwnDay ??
        (created != null ? localDay(() => new Date(created), this.timezone) : today);

      this.log.info({ sessionFile: file, day }, "recovering stale trunk");

      // Recovered at startup (no channel yet): the lifecycle status is buffered and flushed when the
      // channel attaches, so the user still sees each recovered trunk's close.
      await this.closeTrunkSession({ session, day, sessionFile: file }, "lifecycle");
    }

    if (pointer != null && pointer.day < today) this.trunkState.clearActive();
  }

  // ---- internals ------------------------------------------------------------------

  private async handle(message: InboundMessage): Promise<void> {
    this.exchanging = true;

    const startedAt = Date.now();

    this.log.info(
      { origin: message.metadata.origin, channel: message.channel },
      "exchange started",
    );

    try {
      // Ensure the trunk BEFORE the inbound middleware so the boundary middleware can drive collapse on
      // the live trunk. A command middleware that sets `handled` still short-circuits before streaming.
      const active = await this.ensureTrunk();

      await this.runInboundMiddleware(message, active);

      // A middleware (e.g. the commands extension) fully handled the message.
      if (message.metadata.handled === true) return;

      const events = streamPrompt(active.session, renderPrompt(message), this.log);
      await this.channel?.respond({
        message,
        events,
        // The header is turn-scoped: read fresh from this exchange's metadata (never carried across
        // turns). Absent/ malformed descriptor ⇒ no header. Manual commands ack directly (handled) and
        // never reach here; this serves decisions that still stream (auto decisions, rollback replay).
        header: decisionHeaderFrom(message.metadata) ?? undefined,
      });

      await this.runExchangeProcessors(message);
    } finally {
      this.exchanging = false;
      this.rescueOrphanedSteers();
      this.lastExchangeAt = this.now();

      this.log.info(
        {
          origin: message.metadata.origin,
          channel: message.channel,
          handled: message.metadata.handled === true,
          durationMs: Date.now() - startedAt,
        },
        "exchange finished",
      );

      this.scheduleDelivery();
    }
  }

  /**
   * Rescue steered messages the just-ended run never consumed. `submit()` steers
   * mid-exchange input into the live run fire-and-forget; pi drains its steering queue at
   * run-start and after each turn, so a steer landing in the run's final tail is orphaned
   * (the run ends before consuming it). Without this, the message is silently lost — or,
   * via the next `prompt()`'s initial steering drain, mis-attributed to the following
   * exchange. Here, at the definitive run-end moment, pi's pending-steering queue holds
   * exactly this run's orphans: re-enqueue them as their own next exchange (like `/queue`)
   * and clear pi's queue so the next run doesn't re-inject them.
   */
  private rescueOrphanedSteers(): void {
    if (this.steered.length === 0) return;

    try {
      const active = this.active;
      if (active == null) return; // trunk gone before run-end — can't read pending; drop tracked steers
      const pending = new Set(active.session.getSteeringMessages());
      let rescued = 0;
      for (const message of this.steered) {
        if (!pending.has(renderPrompt(message))) continue; // consumed by the run — leave it
        this.log.debug(
          { origin: message.metadata.origin, channel: message.channel },
          "steered message orphaned by run end — enqueuing for the next turn",
        );
        this.enqueue(message);
        rescued += 1;
      }
      if (rescued > 0) {
        this.log.info({ rescued }, "steered messages rescued after run end");
        // Clear pi's steering queue so the next prompt() doesn't drain these rescued
        // orphans back into the following run (surgical — reset() would wipe the whole
        // transcript). Skipped when nothing was rescued: a consumed steer was already
        // drained from the queue by the run, so there is nothing left to clear.
        active.session.agent.clearSteeringQueue();
      }
    } catch (error) {
      this.log.error({ err: error }, "steer rescue failed");
    } finally {
      this.steered.length = 0;
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

  /**
   * Resolve the live branch's state relative to the current base — shared by the inbound middleware
   * snapshot (`buildTrunkInbound`) and trunk-close finalization (`collapseLiveBranchForClose`) so the
   * base-resolution precedence (boomerang's topic base, else the latest branch summary, else null) and
   * the empty-branch guard live in one place rather than drifting between the two paths.
   */
  private trunkBranchState(active: ActiveTrunk) {
    const branchRecords = getBranchRecords(active.session);
    const boomerang = readBoomerangState(active.session);
    const currentBaseId =
      boomerang?.currentTopicBaseId ?? branchRecords.at(-1)?.summaryEntryId ?? null;

    return {
      currentBaseId,
      branchRecords,
      liveBranchId: nextBranchId(branchRecords),
      hasAssistantTurnSinceBase: hasAssistantTurnSinceBase(active.session, currentBaseId),
      checkpointId: boomerang?.checkpointId ?? null,
      lastAutoDecision: boomerang?.lastAutoDecision ?? null,
    };
  }

  /** Snapshot the live trunk for the inbound middleware (base, branch records, checkpoint state). */
  private buildTrunkInbound(active: ActiveTrunk): TrunkInbound {
    return {
      session: active.session,
      sessionFile: active.sessionFile,
      ...this.trunkBranchState(active),
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

    this.log.debug(
      { total: results.length, failed: results.filter((r) => r.status === "rejected").length },
      "exchange processors finished",
    );
  }

  /** Run the trunk's post-processors; returns the number that rejected (0 ⇒ a clean close). */
  private async runPostProcessing(trunk: ActiveTrunk): Promise<number> {
    let failures = 0;

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
        // Arrow form: `status` reads `this.lifecycleActive`/`this.channel`, so the `this` binding
        // must be preserved (a bare `this.status` reference would lose it).
        status: (text: string) => this.status(text),
      },
      log: this.log,
      onProcessorStart: (processor) =>
        this.status(`${processor.statusLabel ?? `Post-processing: ${processor.name}`}…`),
      onProcessorSettled: (_processor, result) => {
        if (result.status === "rejected") failures += 1;
      },
    });

    this.events.emit("session:post-processed", { sessionFile: trunk.sessionFile });

    return failures;
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

    this.log.info({ count: items.length }, "delivery queue flushed");

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

// ---- pending-input (R9) module helpers ----------------------------------------

/** Default pending-input TTL (2 min); kept short so a stale prompt can't capture an unrelated message. */
const DEFAULT_PENDING_INPUT_TTL_MS = 120_000;

/** A pending-input entry: the command awaiting its argument, when it was prompted, and its expiry timer. */
interface PendingInput {
  command: PendingCommand;
  promptedAt: number;
  timer: ReturnType<typeof setTimeout>;
}

/**
 * Argument-taking commands that enter a pending-input flow when invoked bare (R9). The value is the
 * hardcoded (non-LLM) prompt rendered while awaiting the argument. The coordinator owns this set
 * because it already owns the channel-agnostic "/new"/"/queue" prefix-stripping — the intercept must
 * run at the top of submit(), before any middleware (which only runs after prefix-strip and steering).
 */
const PENDING_PROMPTS = {
  new: "What's the first message for the new topic?",
  queue: "What should I queue for the next turn?",
  skill: "Which skill should I load?",
} satisfies Record<string, string>;

/** The bare commands that trigger pending-input (keys of {@link PENDING_PROMPTS}). */
type PendingCommand = keyof typeof PENDING_PROMPTS;

/**
 * Returns the command name when `text` is a bare arg-taking command (the token alone, no argument), or
 * null otherwise. "/new <arg>" (with an argument) is not bare — it flows through the existing
 * prefix-strip → forceNew/queued path unchanged.
 */
const bareArgCommand = (text: string): PendingCommand | null => {
  if (!text.startsWith("/")) return null;
  const token = text.slice(1);
  return token in PENDING_PROMPTS ? (token as PendingCommand) : null;
};

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
