import { join } from "node:path";
import { AgentManager } from "./agent/manager.ts";
import { applyConfigEnv } from "./config/env.ts";
import { loadConfig } from "./config/load.ts";
import { Coordinator } from "./coordinator.ts";
import { createDatabase, runMigrations } from "./db/index.ts";
import { KeyValueState } from "./db/state.ts";
import { EventBus } from "./events.ts";
import { ExtensionHost } from "./extensions/host.ts";
import { firstPartyExtensions } from "./extensions/index.ts";
import { createRegistrations } from "./extensions/registrations.ts";
import { componentLogger, createRootLogger, retainedFiles } from "./log.ts";
import { adaptConfig, adaptWorkspace, adaptWorkspaceData } from "./migration/index.ts";
import { Scheduler } from "./scheduler.ts";
import { TrunkState } from "./sessions/trunk.ts";
import { ShutdownController } from "./shutdown.ts";
import { Workspace } from "./workspace.ts";

export interface RunOptions {
  configPath?: string;
  channel?: string;
}

export const runApp = async (options: RunOptions = {}): Promise<void> => {
  const { config, path: configPath, created } = await loadConfig(options.configPath);

  const workspace = new Workspace(config.workspace.path);
  await workspace.ensure();

  const log = await createRootLogger({
    level: config.logging.level,
    pretty: config.logging.pretty,
    file: config.logging.toFile
      ? {
          path: join(workspace.logsDir, "tachikoma"),
          frequency: config.logging.rotateFrequency,
          retainedFiles: retainedFiles(
            config.logging.retentionDays,
            config.logging.rotateFrequency,
          ),
        }
      : undefined,
  });
  if (created) log.info({ configPath }, "generated default configuration");

  const migrationLog = componentLogger(log, "migration");
  const adapted = await adaptConfig(configPath, migrationLog);

  // config is shared by reference with everything constructed below; assign in
  // place so the translated values flow through without re-wiring runApp.
  if (adapted != null) Object.assign(config, adapted);

  // Apply config-defined env vars before any runtime service or pi session is built,
  // so they are visible app-wide and to anything inheriting the process environment.
  applyConfigEnv(config.env, log);

  await adaptWorkspace(workspace, migrationLog);

  const dbLog = componentLogger(log, "db");
  const db = createDatabase(workspace.databaseFile, dbLog);
  runMigrations(db, dbLog);

  await adaptWorkspaceData(db, workspace, migrationLog);

  const events = new EventBus(componentLogger(log, "events"));
  const scheduler = new Scheduler(componentLogger(log, "scheduler"), config.scheduler.timezone);
  const regs = createRegistrations();

  const agent = new AgentManager(workspace, config, regs, componentLogger(log, "agent"));
  // The coordinator owns a daily trunk via a STABLE `app_state` namespace ("trunk") rather than a
  // session record — the `sessions` table and registry were dropped under the daily-trunk model.
  const trunkState = new TrunkState(new KeyValueState(db, "trunk", dbLog));
  const coordinator = new Coordinator(
    trunkState,
    agent,
    regs,
    events,
    componentLogger(log, "coordinator"),
    config.scheduler.timezone,
    undefined, // now — use the default real-time clock; the pending-input TTL is configured below
    config.coordinator.pendingInputTtlMs,
  );

  const host = new ExtensionHost({
    config,
    workspace,
    log,
    db,
    events,
    scheduler,
    agent,
    coordinator,
    regs,
  });

  log.debug("loading extensions");
  await host.load(firstPartyExtensions);

  log.debug("running bootstrap hooks");
  await host.bootstrap();

  log.debug("recovering stale trunks");
  await coordinator.recoverStaleTrunks();

  // Nightly trunk close in the scheduler timezone: fires the close pipeline only when no
  // exchange is in flight. The lazy stale-day backstop in `ensureTrunk` remains the correctness path
  // if the process is down at this hour.
  scheduler.cron("trunk-nightly-close", `0 ${config.scheduler.nightlyCloseHour} * * *`, () =>
    coordinator.closeTrunkIfDue(),
  );

  const channelName = options.channel ?? config.channels.default;
  const channel = regs.channels.get(channelName);

  if (channel == null) {
    const available = [...regs.channels.keys()].join(", ");
    throw new Error(`Unknown channel "${channelName}" — available: ${available}`);
  }

  coordinator.attachChannel(channel);

  const abort = new AbortController();
  const shutdown = new ShutdownController({ abort, log });
  process.once("SIGINT", () => shutdown.trigger("SIGINT"));
  process.once("SIGTERM", () => shutdown.trigger("SIGTERM"));
  process.once("uncaughtException", (err) => shutdown.trigger("uncaughtException", err));
  process.once("unhandledRejection", (reason) => shutdown.trigger("unhandledRejection", reason));

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

  // A crash-initiated drain completed: exit non-zero so supervisors (e.g. systemd) restart us.
  // exitCode (not process.exit) lets the event loop empty and pino flush; the controller's own
  // timeout/second-signal backstop still uses process.exit(1).
  if (shutdown.didCrash) process.exitCode = 1;
};
