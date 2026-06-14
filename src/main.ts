#!/usr/bin/env node
import { parseArgs } from "node:util";

import { runApp } from "./app.ts";

const { values } = parseArgs({
  // Tolerate a stray positional (e.g. the legacy `run` subcommand) instead of crashing.
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

Options:
  -c, --channel <name>   Channel to run (default: from config, usually "repl")
      --config <path>    Config file (default: ~/.config/tachikoma/config.toml)
  -h, --help             Show this help
`);
  process.exit(0);
}

runApp({ channel: values.channel, configPath: values.config }).catch((error) => {
  console.error(error instanceof Error ? (error.stack ?? error.message) : error);
  process.exit(1);
});
