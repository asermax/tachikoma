import type { AgentManager } from "../agent/manager.ts";
import { SideRunner } from "../agent/side-run.ts";
import { parseWithSchema } from "../config/parse.ts";
import type { Config } from "../config/schema.ts";
import type { Coordinator } from "../coordinator.ts";
import type { AppDatabase } from "../db/index.ts";
import { KeyValueState } from "../db/state.ts";
import type { EventBus } from "../events.ts";
import { componentLogger, type Logger } from "../log.ts";
import type { Scheduler } from "../scheduler.ts";
import type { SessionRegistry } from "../sessions/registry.ts";
import type { Workspace } from "../workspace.ts";
import {
  type AppContext,
  type ContextBlock,
  type ContextProvider,
  type ContextProviderInput,
  type PostProcessingPhase,
  type PostProcessor,
  type PostProcessorContext,
  SESSION_SCOPES,
  type TachikomaExtension,
  type UseFactoryOptions,
} from "./api.ts";
import type { Registrations } from "./registrations.ts";

const POST_PROCESSING_PHASE_ORDER: PostProcessingPhase[] = ["main", "preFinalize", "finalize"];

/** Run context providers, dropping nulls and isolating failures (used for headless/background runs). */
const collectContextBlocks = async (
  providers: ContextProvider[],
  input: ContextProviderInput,
  log: Logger,
): Promise<ContextBlock[]> => {
  const results = await Promise.allSettled(providers.map((provider) => provider.provide(input)));

  const blocks: ContextBlock[] = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      if (result.value != null) blocks.push(result.value);
    } else {
      log.error(
        { provider: providers[index]?.name, err: result.reason },
        "context provider failed",
      );
    }
  });

  return blocks;
};

/** Run post-processors once in phase order, error-isolated (no per-session state tracking). */
const runPostProcessorsOnce = async (
  processors: PostProcessor[],
  context: PostProcessorContext,
): Promise<void> => {
  for (const phase of POST_PROCESSING_PHASE_ORDER) {
    const phaseProcessors = processors.filter((processor) => (processor.phase ?? "main") === phase);

    const results = await Promise.allSettled(
      phaseProcessors.map((processor) =>
        processor.process({ ...context, log: context.log.child({ processor: processor.name }) }),
      ),
    );

    results.forEach((result, index) => {
      if (result.status === "rejected") {
        context.log.error(
          { processor: phaseProcessors[index]?.name, err: result.reason },
          "post-processor failed",
        );
      }
    });
  }
};

/**
 * Resolve which session contexts a factory binds into from its `use` options. Pure so the
 * mapping is unit-testable; membership-based so an out-of-union scope binds nothing.
 */
export const factoryBindingTargets = (
  options?: Pick<UseFactoryOptions, "sessionScopes">,
): { main: boolean; background: boolean } => {
  const scopes = options?.sessionScopes ?? [SESSION_SCOPES.main];

  return {
    main: scopes.includes(SESSION_SCOPES.main),
    background: scopes.includes(SESSION_SCOPES.background),
  };
};

export interface HostServices {
  config: Config;
  workspace: Workspace;
  log: Logger;
  db: AppDatabase;
  events: EventBus;
  scheduler: Scheduler;
  agent: AgentManager;
  registry: SessionRegistry;
  coordinator: Coordinator;
  regs: Registrations;
}

/** Fallback when the `external` extension does not declare its own setup timeout. */
export const DEFAULT_EXTERNAL_SETUP_TIMEOUT_MS = 30_000;

interface QueuedExtension {
  extension: TachikomaExtension<never>;
  /** Third-party extensions are isolated on setup; first-party fail hard. */
  external: boolean;
  setupTimeoutMs?: number;
}

const withTimeout = <T>(promise: Promise<T>, timeoutMs: number): Promise<T> => {
  let timer: ReturnType<typeof setTimeout>;

  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`setup timed out after ${timeoutMs}ms`)), timeoutMs);
  });

  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
};

export class ExtensionHost {
  private readonly services: HostServices;

  constructor(services: HostServices) {
    this.services = services;
  }

  private readonly queue: QueuedExtension[] = [];

  async load(extensions: TachikomaExtension<never>[]): Promise<void> {
    this.queue.push(...extensions.map((extension) => ({ extension, external: false })));

    // Extensions may enqueue further extensions (third-party) while loading.
    for (let index = 0; index < this.queue.length; index += 1) {
      const { extension, external, setupTimeoutMs } = this.queue[index] as QueuedExtension;
      const app = this.buildContext(extension, external);

      // First-party setups fail hard — a core bug must surface, not be swallowed.
      // External (third-party) setups are isolated: a throw or hang skips that one
      // extension and lets startup continue (see external-extensions design).
      if (!external) {
        await extension.setup(app as AppContext<never>);
        this.services.log.debug({ extension: extension.name }, "extension loaded");
        continue;
      }

      try {
        await withTimeout(
          Promise.resolve(extension.setup(app as AppContext<never>)),
          setupTimeoutMs ?? DEFAULT_EXTERNAL_SETUP_TIMEOUT_MS,
        );
        this.services.log.debug({ extension: extension.name }, "external extension loaded");
      } catch (error) {
        this.services.log.warn(
          { extension: extension.name, err: error },
          "external extension setup failed or timed out — skipping",
        );
      }
    }
  }

  async bootstrap(): Promise<void> {
    for (const { name, hook, external } of this.services.regs.bootstrapHooks) {
      this.services.log.debug({ hook: name }, "bootstrap hook");

      // Mirror the setup-phase rule: a third-party hook is isolated, a first-party
      // hook fails hard (core-shell R14) so core bootstrap bugs surface.
      if (!external) {
        await hook();
        continue;
      }

      try {
        await hook();
      } catch (error) {
        this.services.log.warn(
          { hook: name, err: error },
          "external bootstrap hook failed — skipping",
        );
      }
    }
  }

  private buildContext(extension: TachikomaExtension<never>, external = false): AppContext {
    const { services } = this;
    const log = componentLogger(services.log, extension.name);

    const rawSection = services.config.extensions[extension.name] ?? {};
    const extensionConfig =
      extension.configSchema != null
        ? parseWithSchema(extension.configSchema, rawSection, `extensions.${extension.name}`)
        : rawSection;

    return {
      config: services.config,
      extensionConfig,
      workspace: services.workspace,
      log,
      db: services.db,
      state: new KeyValueState(services.db, extension.name),
      events: services.events,
      scheduler: services.scheduler,

      sessions: {
        current: () => services.coordinator.current(),
        get: (id) => services.registry.get(id),
        update: (id, patch) => services.registry.update(id, patch),
        listResumable: () =>
          services.registry.listResumable(services.config.sessions.resumeWindowSeconds),
        recordChannelMessage: (channel, messageId, sessionId, direction) =>
          services.registry.recordChannelMessage(channel, messageId, sessionId, direction),
        findSessionByMessageId: (channel, messageId) =>
          services.registry.findSessionByMessageId(channel, messageId),
        close: () => services.coordinator.closeActiveSession(),
        closeIfIdle: () => services.coordinator.closeActiveSessionIfIdle(),
        abortExchange: () => services.coordinator.abortExchange(),
        onOpen: (hook) => services.regs.sessionOpenHooks.push(hook),
        onExchange: (processor) => services.regs.exchangeProcessors.push(processor),
        registerProcessor: (processor) => services.regs.postProcessors.push(processor),
        runPostProcessors: (context) =>
          runPostProcessorsOnce(services.regs.postProcessors, context),
      },

      channels: {
        register: (channel) => services.regs.channels.set(channel.name, channel),
        deliver: (delivery) => services.coordinator.deliver(delivery),
      },

      agent: {
        use: (factory, options) => {
          const targets = factoryBindingTargets(options);

          if (targets.main) services.regs.piFactories.push(factory);
          if (targets.background) services.regs.backgroundFactories.push(factory);
        },
        systemPrompt: (builder) => services.regs.systemPromptBuilders.push(builder),
        provideContext: (provider) => services.regs.contextProviders.push(provider),
        collectContext: (input) => collectContextBlocks(services.regs.contextProviders, input, log),
        models: services.agent.tiers,
        side: new SideRunner(services.agent, log),
      },

      inbound: {
        use: (middleware) => services.regs.inboundMiddleware.push(middleware),
      },

      bootstrap: (name, hook) =>
        services.regs.bootstrapHooks.push({ name: `${extension.name}:${name}`, hook, external }),

      status: (text) => services.coordinator.status(text),

      registerExtension: (nested, options) =>
        this.queue.push({
          extension: nested,
          external: true,
          setupTimeoutMs: options?.setupTimeoutMs,
        }),
    };
  }
}
