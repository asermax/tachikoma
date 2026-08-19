#!/usr/bin/env node
import { parseArgs } from "node:util";

import { runApp } from "./app.ts";

const { positionals, values } = parseArgs({
  // Positionals are parsed so they can be rejected below — only the legacy `run`
  // subcommand is tolerated; anything else used to silently start a full daemon.
  allowPositionals: true,
  options: {
    channel: { type: "string", short: "c" },
    config: { type: "string" },
    help: { type: "boolean", short: "h" },
  },
});

if (values.help === true) {
  console.log(`tachikoma — proactive personal assistant

Usage: tachikoma [options]

Tachikoma takes no subcommands — running it starts the assistant daemon.

Options:
  -c, --channel <name>   Channel to run (default: from config, "telegram")
      --config <path>    Config file (default: ~/.config/tachikoma/config.toml)
  -h, --help             Show this help
`);
  process.exit(0);
}

const tolerated =
  positionals.length === 0 || (positionals.length === 1 && positionals[0] === "run");
if (!tolerated) {
  // Fail fast: a stray positional (e.g. `tachikoma workflow | head`) used to start a
  // full daemon that could outlive the pipe and run alongside the real instance.
  console.error(
    `Unknown argument${positionals.length > 1 ? "s" : ""}: ${positionals.join(", ")}\n` +
      "tachikoma takes no subcommands — it starts the assistant daemon.\n" +
      'Run "tachikoma --help" for usage.',
  );
  process.exit(1);
}

// console.error (not the pino logger) because a startup failure may precede logger
// creation; once the logger exists, ShutdownController routes failures through pino.
runApp({ channel: values.channel, configPath: values.config }).catch((error) => {
  console.error(error instanceof Error ? (error.stack ?? error.message) : error);
  process.exit(1);
});
