import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";

import type { Channel } from "../channels/types.ts";
import type { SessionRecord } from "../db/core-schema.ts";
import type {
  ContextProvider,
  ExchangeProcessor,
  InboundMiddleware,
  PostProcessor,
} from "./api.ts";

export interface BootstrapHook {
  name: string;
  hook: () => void | Promise<void>;
  /** Hooks from third-party extensions are isolated; first-party hooks fail hard. */
  external?: boolean;
}

/** Mutable registries filled by extensions during setup and read by core at runtime. */
export interface Registrations {
  piFactories: ExtensionFactory[];
  /** Subset of piFactories whose tools/resources are also bound into background task runs. */
  backgroundFactories: ExtensionFactory[];
  systemPromptBuilders: (() => string)[];
  contextProviders: ContextProvider[];
  exchangeProcessors: ExchangeProcessor[];
  postProcessors: PostProcessor[];
  inboundMiddleware: InboundMiddleware[];
  sessionOpenHooks: ((session: SessionRecord) => void | Promise<void>)[];
  channels: Map<string, Channel>;
  bootstrapHooks: BootstrapHook[];
}

export const createRegistrations = (): Registrations => ({
  piFactories: [],
  backgroundFactories: [],
  systemPromptBuilders: [],
  contextProviders: [],
  exchangeProcessors: [],
  postProcessors: [],
  inboundMiddleware: [],
  sessionOpenHooks: [],
  channels: new Map(),
  bootstrapHooks: [],
});
