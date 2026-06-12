import { AgentManager } from "./agent/manager.ts";
import { loadConfig } from "./config/load.ts";
import { Coordinator } from "./coordinator.ts";
import { createDatabase, runMigrations } from "./db/index.ts";
import { EventBus } from "./events.ts";
import { ExtensionHost } from "./extensions/host.ts";
import { firstPartyExtensions } from "./extensions/index.ts";
import { createRegistrations } from "./extensions/registrations.ts";
import { componentLogger, createRootLogger } from "./log.ts";
import { Scheduler } from "./scheduler.ts";
import { SessionRegistry } from "./sessions/registry.ts";
import { Workspace } from "./workspace.ts";

export interface RunOptions {
  configPath?: string;
  channel?: string;
}

export const runApp = async (options: RunOptions = {}): Promise<void> => {
  const { config, path: configPath, created } = await loadConfig(options.configPath);

  const log = createRootLogger(config.logging);
  if (created) log.info({ configPath }, "generated default configuration");

  const workspace = new Workspace(config.workspace.path);
  await workspace.ensure();

  const db = createDatabase(workspace.databaseFile);
  runMigrations(db);

  const events = new EventBus(componentLogger(log, "events"));
  const scheduler = new Scheduler(componentLogger(log, "scheduler"), config.scheduler.timezone);
  const regs = createRegistrations();

  const agent = new AgentManager(workspace, config, regs, componentLogger(log, "agent"));
  const registry = new SessionRegistry(db);
  const coordinator = new Coordinator(
    config,
    registry,
    agent,
    regs,
    events,
    componentLogger(log, "coordinator"),
  );

  regs.piFactories.push(coordinator.hostFactory());

  const host = new ExtensionHost({
    config,
    workspace,
    log,
    db,
    events,
    scheduler,
    agent,
    registry,
    coordinator,
    regs,
  });

  await host.load(firstPartyExtensions);
  await host.bootstrap();
  await coordinator.recoverDanglingSessions();

  const channelName = options.channel ?? config.channels.default;
  const channel = regs.channels.get(channelName);

  if (channel == null) {
    const available = [...regs.channels.keys()].join(", ");
    throw new Error(`Unknown channel "${channelName}" — available: ${available}`);
  }

  coordinator.attachChannel(channel);

  const abort = new AbortController();
  const shutdown = (signal: string) => {
    log.info({ signal }, "shutting down");
    abort.abort();
  };
  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));

  await channel.start({
    log: componentLogger(log, channelName),
    submit: (message) => coordinator.submit(message),
  });

  log.info({ channel: channelName, workspace: workspace.root }, "tachikoma ready");

  try {
    await coordinator.run(abort.signal);
  } finally {
    await channel.stop();
    scheduler.stopAll();
  }
};
