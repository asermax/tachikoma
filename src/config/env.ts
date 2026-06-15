import type { Logger } from "../log.ts";
import type { Config } from "./schema.ts";

/**
 * Apply the config `[env]` section to `process.env`, making the variables visible
 * app-wide and to anything that inherits the process environment (pi sessions,
 * spawned agent tools, detached processes). Config-defined values overwrite any
 * existing same-named variable. Only the keys are logged — values may be secrets.
 */
export const applyConfigEnv = (env: Config["env"], log: Logger): void => {
  const keys = Object.keys(env);

  if (keys.length === 0) return;

  for (const key of keys) {
    process.env[key] = env[key];
  }

  log.info({ keys }, "applied config environment variables");
};
