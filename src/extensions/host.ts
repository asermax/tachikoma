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
import type { AppContext, TachikomaExtension } from "./api.ts";
import type { Registrations } from "./registrations.ts";

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

export class ExtensionHost {
  private readonly services: HostServices;

  constructor(services: HostServices) {
    this.services = services;
  }

  async load(extensions: TachikomaExtension<never>[]): Promise<void> {
    for (const extension of extensions) {
      const app = this.buildContext(extension);

      await extension.setup(app as AppContext<never>);
      this.services.log.debug({ extension: extension.name }, "extension loaded");
    }
  }

  async bootstrap(): Promise<void> {
    for (const { name, hook } of this.services.regs.bootstrapHooks) {
      this.services.log.debug({ hook: name }, "bootstrap hook");
      await hook();
    }
  }

  private buildContext(extension: TachikomaExtension<never>): AppContext {
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
        close: () => services.coordinator.closeActiveSession(),
        onOpen: (hook) => services.regs.sessionOpenHooks.push(hook),
        onExchange: (processor) => services.regs.exchangeProcessors.push(processor),
        registerProcessor: (processor) => services.regs.postProcessors.push(processor),
      },

      channels: {
        register: (channel) => services.regs.channels.set(channel.name, channel),
        deliver: (delivery) => services.coordinator.deliver(delivery),
      },

      agent: {
        use: (factory) => services.regs.piFactories.push(factory),
        systemPrompt: (builder) => services.regs.systemPromptBuilders.push(builder),
        provideContext: (provider) => services.regs.contextProviders.push(provider),
        models: services.agent.tiers,
        side: new SideRunner(services.agent, log),
      },

      inbound: {
        use: (middleware) => services.regs.inboundMiddleware.push(middleware),
      },

      bootstrap: (name, hook) =>
        services.regs.bootstrapHooks.push({ name: `${extension.name}:${name}`, hook }),

      status: (text) => services.coordinator.status(text),
    };
  }
}
