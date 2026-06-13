import { readFile, writeFile } from "node:fs/promises";
import { parse as parseToml, stringify as stringifyToml } from "smol-toml";

import { loadConfig } from "../config/load.ts";
import type { Config } from "../config/schema.ts";
import type { Logger } from "../log.ts";
import { type Ask, createAsk } from "./ask.ts";

const OLD_ROLE_KEYS = {
  model: "main",
  searcher_model: "searcher",
  processor_model: "processor",
  classifier_model: "classifier",
} as const;

const asTable = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value != null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

export const isOldShapeConfig = (raw: Record<string, unknown>): boolean => {
  const agent = asTable(raw.agent);

  return (
    asTable(raw.telegram) != null ||
    typeof raw.channel === "string" ||
    (agent != null && Object.keys(OLD_ROLE_KEYS).some((key) => key in agent))
  );
};

const translateOldConfig = async (
  raw: Record<string, unknown>,
  log: Logger,
  ask: Ask,
): Promise<Record<string, unknown>> => {
  const result: Record<string, unknown> = {};

  const oldWorkspace = asTable(raw.workspace);

  if (typeof oldWorkspace?.path === "string") result.workspace = { path: oldWorkspace.path };

  const oldAgent = asTable(raw.agent);
  const agent: Record<string, string> = {};

  if (oldAgent != null) {
    for (const [oldKey, role] of Object.entries(OLD_ROLE_KEYS)) {
      const value = oldAgent[oldKey];

      if (typeof value !== "string" || value === "") continue;

      // Python values were bare SDK aliases ("opus"); pi needs "provider/model-id".
      const candidate = value.includes("/") ? value : `anthropic/${value}`;

      if (await ask(`Set agent ${role} model to "${candidate}" (was "${value}")?`)) {
        agent[role] = candidate;
      } else {
        log.warn(
          { role, value },
          "agent role left unset — configure it under [agent] in the new config if needed",
        );
      }
    }
  }

  if (Object.keys(agent).length > 0) result.agent = agent;

  const oldLogging = asTable(raw.logging);

  if (oldLogging != null) {
    const logging: Record<string, unknown> = {};

    if (typeof oldLogging.level === "string") logging.level = oldLogging.level.toLowerCase();
    if (typeof oldLogging.console === "boolean") logging.pretty = oldLogging.console;
    if (Object.keys(logging).length > 0) result.logging = logging;
  }

  if (typeof raw.channel === "string") result.channels = { default: raw.channel };

  const resumeWindow = oldAgent?.session_resume_window;

  if (typeof resumeWindow === "number") result.sessions = { resumeWindowSeconds: resumeWindow };

  const timezone = asTable(raw.tasks)?.timezone;

  if (typeof timezone === "string" && timezone !== "") result.scheduler = { timezone };

  const oldTelegram = asTable(raw.telegram);

  if (oldTelegram != null) {
    const telegram: Record<string, unknown> = {};

    if (typeof oldTelegram.bot_token === "string") telegram.botToken = oldTelegram.bot_token;

    if (typeof oldTelegram.authorized_chat_id === "number") {
      telegram.chatId = oldTelegram.authorized_chat_id;
    }

    if (typeof oldTelegram.push_notifications === "boolean") {
      telegram.pushNotifications = oldTelegram.push_notifications;
    }

    const extraRoots = asTable(oldTelegram.send_file)?.extra_roots;

    if (Array.isArray(extraRoots)) {
      telegram.extraFileRoots = extraRoots.filter((root) => typeof root === "string");
    }

    if (oldTelegram.inbound_reactions != null) {
      log.info("telegram inbound_reactions has no pi equivalent — dropped");
    }

    result.extensions = { telegram };
  }

  const carried = new Set(["workspace", "agent", "logging", "channel", "telegram"]);
  const dropped = Object.keys(raw).filter((key) => !carried.has(key));

  if (dropped.length > 0) {
    log.info(
      { sections: dropped },
      "config sections without a pi equivalent were not ported (except tasks.timezone → scheduler.timezone); originals preserved in the backup",
    );
  }

  return result;
};

/**
 * Detect a config.toml written by the Python implementation, back it up to
 * `<path>.python-backup`, write a translated new-layout file, and return the
 * reloaded Config. Returns null when the file is absent or already new-shape.
 */
export const adaptConfig = async (
  path: string,
  log: Logger,
  ask: Ask = createAsk(log),
): Promise<Config | null> => {
  let raw: string;

  try {
    raw = await readFile(path, "utf8");
  } catch {
    return null;
  }

  let parsed: Record<string, unknown>;

  try {
    parsed = parseToml(raw);
  } catch (error) {
    log.warn(
      { path, error },
      "could not parse config for Python-era detection — leaving it untouched",
    );
    return null;
  }

  if (!isOldShapeConfig(parsed)) return null;

  log.warn({ path }, "Python-era config detected — translating to the new layout");

  const translated = await translateOldConfig(parsed, log, ask);
  const backupPath = `${path}.python-backup`;

  await writeFile(backupPath, raw, "utf8");

  const header = `# Tachikoma configuration\n# Translated from the Python-era config; the original is preserved at\n# ${backupPath}\n\n`;
  await writeFile(path, `${header}${stringifyToml(translated)}\n`, "utf8");

  log.info({ path, backupPath }, "config translated — continuing with the new layout");

  const { config } = await loadConfig(path);
  return config;
};
