import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";

import type { Channel } from "../channels/types.ts";
import type { SessionRecord } from "../db/core-schema.ts";
import type { ExchangeProcessor, InboundMiddleware, PostProcessor } from "./api.ts";

export interface BootstrapHook {
  name: string;
  hook: () => void | Promise<void>;
  /** Hooks from third-party extensions are isolated; first-party hooks fail hard. */
  external?: boolean;
}

/** Mutable registries filled by extensions during setup and read by core at runtime. */
export interface Registrations {
  piFactories: ExtensionFactory[];
  /**
   * Factories whose tools/resources are bound into background task runs. Independent of
   * piFactories — a factory scoped to `"background"` only lives here without being a main factory.
   */
  backgroundFactories: ExtensionFactory[];
  systemPromptBuilders: (() => string)[];
  exchangeProcessors: ExchangeProcessor[];
  postProcessors: PostProcessor[];
  inboundMiddleware: InboundMiddleware[];
  sessionOpenHooks: ((session: SessionRecord) => void | Promise<void>)[];
  channels: Map<string, Channel>;
  bootstrapHooks: BootstrapHook[];
  shutdownHooks: { name: string; hook: () => void | Promise<void> }[];
}

export const createRegistrations = (): Registrations => ({
  piFactories: [],
  backgroundFactories: [],
  systemPromptBuilders: [],
  exchangeProcessors: [],
  postProcessors: [],
  inboundMiddleware: [],
  sessionOpenHooks: [],
  channels: new Map(),
  bootstrapHooks: [],
  shutdownHooks: [],
});
