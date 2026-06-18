import type { AgentManager } from "../agent/manager.ts";
import { SideRunner } from "../agent/side-run.ts";
import { parseWithSchema } from "../config/parse.ts";
import type { Config } from "../config/schema.ts";
import type { Coordinator } from "../coordinator.ts";
import type { AppDatabase } from "../db/index.ts";
import { KeyValueState } from "../db/state.ts";
import type { EventBus } from "../events.ts";
import { commitAll, commitAllDeterministic } from "../git/commit.ts";
import { createCommitAgent } from "../git/commit-agent.ts";
import { smartPull, smartPush } from "../git/sync.ts";
import { componentLogger, type Logger } from "../log.ts";
import type { Scheduler } from "../scheduler.ts";
import type { Workspace } from "../workspace.ts";
import {
  type AgentExtensionFactory,
  type AppContext,
  SESSION_SCOPES,
  type TachikomaExtension,
  type UseFactoryOptions,
} from "./api.ts";
import { runPhasedPostProcessors } from "./post-processing.ts";
import type { Registrations } from "./registrations.ts";

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

    this.services.log.debug({ count: extensions.length }, "loading extensions");

    let loaded = 0;
    let skipped = 0;

    // Extensions may enqueue further extensions (third-party) while loading.
    for (let index = 0; index < this.queue.length; index += 1) {
      const { extension, external, setupTimeoutMs } = this.queue[index] as QueuedExtension;

      // First-party setups fail hard — a core bug must surface, not be swallowed.
      // External (third-party) setups are isolated: a throw or hang skips that one
      // extension and lets startup continue (see external-extensions design).
      if (!external) {
        await extension.setup(this.buildContext(extension, external) as AppContext<never>);
        loaded += 1;
        this.services.log.debug({ extension: extension.name }, "extension loaded");
        continue;
      }

      // buildContext parses the external config and can itself throw — keep it inside the
      // isolation boundary so a bad third-party config is skipped, not fatal.
      try {
        const app = this.buildContext(extension, external);

        await withTimeout(
          Promise.resolve(extension.setup(app as AppContext<never>)),
          setupTimeoutMs ?? DEFAULT_EXTERNAL_SETUP_TIMEOUT_MS,
        );
        loaded += 1;
        this.services.log.debug({ extension: extension.name }, "external extension loaded");
      } catch (error) {
        skipped += 1;
        this.services.log.warn(
          { extension: extension.name, err: error },
          "external extension setup failed or timed out — skipping",
        );
      }
    }

    this.services.log.info({ loaded, skipped }, "extensions loaded");
  }

  async bootstrap(): Promise<void> {
    let skipped = 0;

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
        skipped += 1;
        this.services.log.warn(
          { hook: name, err: error },
          "external bootstrap hook failed — skipping",
        );
      }
    }

    this.services.log.info(
      { count: this.services.regs.bootstrapHooks.length, skipped },
      "bootstrap hooks run",
    );
  }

  private buildContext(extension: TachikomaExtension<never>, external = false): AppContext {
    const { services } = this;
    const log = componentLogger(services.log, extension.name);

    const rawSection = services.config.extensions[extension.name] ?? {};
    const extensionConfig =
      extension.configSchema != null
        ? parseWithSchema(extension.configSchema, rawSection, `extensions.${extension.name}`)
        : rawSection;

    const side = new SideRunner(services.agent, log);

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
        close: () => services.coordinator.closeTrunk(),
        abortExchange: () => services.coordinator.abortExchange(),
        activeTrunkSession: () => services.coordinator.activeTrunkSession(),
        onOpen: (hook) => services.regs.sessionOpenHooks.push(hook),
        onExchange: (processor) => services.regs.exchangeProcessors.push(processor),
        registerProcessor: (processor) => services.regs.postProcessors.push(processor),
        runPostProcessors: (context) =>
          runPhasedPostProcessors({
            processors: services.regs.postProcessors,
            context,
            log: context.log,
          }),
      },

      channels: {
        register: (channel) => services.regs.channels.set(channel.name, channel),
        deliver: (delivery) => services.coordinator.deliver(delivery),
      },

      agent: {
        use: (factory: AgentExtensionFactory, options?: UseFactoryOptions) => {
          const targets = factoryBindingTargets(options);

          if (targets.main) services.regs.piFactories.push(factory);
          if (targets.background) services.regs.backgroundFactories.push(factory);
        },
        models: services.agent.tiers,
        side,
        forkAndContinue: (sourceSessionFile, prompt, tier, tools) =>
          services.agent.forkAndContinue(sourceSessionFile, prompt, tier, tools),
        isForking: () => services.agent.isForking(),
        shadowFork: (sourceSessionFile, options) =>
          services.agent.shadowFork(sourceSessionFile, options),
        branchFile: (sourceSessionFile, leafId) =>
          services.agent.branchFile(sourceSessionFile, leafId),
      },

      inbound: {
        use: (middleware) => services.regs.inboundMiddleware.push(middleware),
      },

      git: {
        commitAll: ({ log: callLog, ...options }) => commitAll({ ...options, log: callLog ?? log }),
        commitAllDeterministic: ({ log: callLog, ...options }) =>
          commitAllDeterministic({ ...options, log: callLog ?? log }),
        createCommitAgent: (mode) => createCommitAgent(side, mode),
        smartPush: (cwd, remote, branch, options) =>
          smartPush(cwd, remote, branch, options?.log ?? log, options?.resolver),
        smartPull: (cwd, remote, branch, options) =>
          smartPull(cwd, remote, branch, options?.log ?? log, options?.resolver),
      },

      bootstrap: (name, hook) =>
        services.regs.bootstrapHooks.push({ name: `${extension.name}:${name}`, hook, external }),

      onShutdown: (name, hook) =>
        services.regs.shutdownHooks.push({ name: `${extension.name}:${name}`, hook }),

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
