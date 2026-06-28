import type { Channel } from "../channels/types.ts";
import type {
  AgentExtensionFactory,
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
  piFactories: AgentExtensionFactory[];
  /**
   * Factories whose tools/resources are bound into background task runs. Independent of
   * piFactories — a factory scoped to `"background"` only lives here without being a main factory.
   */
  backgroundFactories: AgentExtensionFactory[];
  /**
   * Factories whose tools/resources are bound into delegated subagent runs that request extension
   * tools (via `delegate_to_agent`'s `extensionTools`). Independent of piFactories/backgroundFactories
   * — a factory scoped to `"subagent"` only lives here without being a main or background factory.
   */
  subagentFactories: AgentExtensionFactory[];
  exchangeProcessors: ExchangeProcessor[];
  postProcessors: PostProcessor[];
  inboundMiddleware: InboundMiddleware[];
  /** Fired once per day when the trunk opens (daily-trunk lifecycle). */
  sessionOpenHooks: (() => void | Promise<void>)[];
  channels: Map<string, Channel>;
  bootstrapHooks: BootstrapHook[];
  shutdownHooks: { name: string; hook: () => void | Promise<void> }[];
}

export const createRegistrations = (): Registrations => ({
  piFactories: [],
  backgroundFactories: [],
  subagentFactories: [],
  exchangeProcessors: [],
  postProcessors: [],
  inboundMiddleware: [],
  sessionOpenHooks: [],
  channels: new Map(),
  bootstrapHooks: [],
  shutdownHooks: [],
});
