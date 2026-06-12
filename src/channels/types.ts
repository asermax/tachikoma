import type { AgentEvent } from "../domain/agent-events.ts";
import type { InboundMessage } from "../domain/message.ts";
import type { Logger } from "../log.ts";

export interface ChannelRuntime {
  log: Logger;
  submit(message: InboundMessage): void;
}

export interface Exchange {
  message: InboundMessage;
  events: AsyncIterable<AgentEvent>;
}

export const DELIVERY_GATES = {
  idle: "idle",
  immediate: "immediate",
} as const;

export type DeliveryGate = keyof typeof DELIVERY_GATES;

export interface Delivery {
  text: string;
  gate?: DeliveryGate;
  maxHoldSeconds?: number;
  metadata?: Record<string, unknown>;
}

export interface Channel {
  readonly name: string;

  start(runtime: ChannelRuntime): Promise<void>;

  /** Render one full agent exchange (consume the event stream to completion). */
  respond(exchange: Exchange): Promise<void>;

  /** Render a background-originated item (task results, notifications). */
  deliver(delivery: Delivery): Promise<void>;

  stop(): Promise<void>;
}
