import type { AgentEvent } from "../domain/agent-events.ts";
import type { DecisionHeader, InboundMessage } from "../domain/message.ts";
import type { Logger } from "../log.ts";

export interface ChannelRuntime {
  log: Logger;
  submit(message: InboundMessage): void;
}

export interface Exchange {
  message: InboundMessage;
  events: AsyncIterable<AgentEvent>;
  /**
   * Optional turn-scoped decision header (R8): a branching-decision label anchored above the streamed
   * text for this one response. The coordinator forwards `message.metadata.decisionHeader` fresh each
   * exchange (never carried across turns); the channel drops it after the exchange. Absent ⇒ no header.
   */
  header?: DecisionHeader;
}

export const DELIVERY_TIERS = {
  urgent: "urgent",
  normal: "normal",
  low: "low",
} as const;

export type DeliveryTier = keyof typeof DELIVERY_TIERS;

export interface Delivery {
  text: string;
  /** Queue tier governing ordering and idle/max-hold timing. Default "normal". */
  tier?: DeliveryTier;
  /**
   * Synchronous channel render for command UI (e.g. the /new ack) — bypasses the
   * priority queue entirely. Background notifications never set this.
   */
  immediate?: boolean;
  metadata?: Record<string, unknown>;
}

export interface Channel {
  readonly name: string;

  start(runtime: ChannelRuntime): Promise<void>;

  /** Render one full agent exchange (consume the event stream to completion). */
  respond(exchange: Exchange): Promise<void>;

  /** Render a background-originated item (task results, notifications). */
  deliver(delivery: Delivery): Promise<void>;

  /** Render a transient pipeline status line ("Gathering context…"). Optional. */
  status?(text: string): void;

  /**
   * Render shutdown-sequence progress on a dedicated, persistent message that
   * survives the teardown (the normal `status` surface — a streaming renderer —
   * is gone by then). Awaited by the coordinator so the update lands before the
   * process exits. Optional; channels without it fall back to `status`.
   */
  shutdownStatus?(text: string): Promise<void>;

  /**
   * Render trunk-lifecycle progress (nightly close, stale-trunk recovery) on a
   * dedicated, persistent message that survives into the next exchange — unlike
   * `status` (a reclaimable preparation lead-in) it is never reclaimed or deleted
   * by a streamed response. One message per lifecycle event: the first call with
   * `fresh` starts a new message, later calls edit it in place. Optional; channels
   * without it fall back to `status`.
   */
  lifecycleStatus?(text: string, fresh?: boolean): Promise<void>;

  stop(): Promise<void>;
}
