import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { parse as parseToml, TomlError } from "smol-toml";

import { expandHome } from "../workspace.ts";
import { DEFAULT_CONFIG_TEMPLATE } from "./default-template.ts";
import { ConfigError, parseWithSchema } from "./parse.ts";
import { type Config, ConfigSchema } from "./schema.ts";
import { resolveTimezone } from "./timezone.ts";

export const defaultConfigPath = (): string =>
  join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "tachikoma", "config.toml");

export interface LoadedConfig {
  config: Config;
  path: string;
  created: boolean;
}

export const loadConfig = async (path?: string): Promise<LoadedConfig> => {
  const configPath = expandHome(path ?? defaultConfigPath());

  let raw: string;
  let created = false;

  try {
    raw = await readFile(configPath, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;

    await mkdir(dirname(configPath), { recursive: true });
    await writeFile(configPath, DEFAULT_CONFIG_TEMPLATE, "utf8");
    raw = DEFAULT_CONFIG_TEMPLATE;
    created = true;
  }

  const label = `config at ${configPath}`;

  let parsed: unknown;

  try {
    parsed = parseToml(raw);
  } catch (error) {
    if (!(error instanceof TomlError)) throw error;

    throw new ConfigError(
      `Invalid ${label}:\n  line ${error.line}, column ${error.column}: ${error.message}\n${error.codeblock}`,
    );
  }

  const config = parseWithSchema(ConfigSchema, parsed, label);

  config.scheduler.timezone = resolveTimezone(config.scheduler.timezone, label);

  return { config, path: configPath, created };
};
