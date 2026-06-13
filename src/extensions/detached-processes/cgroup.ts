import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Logger } from "../../log.ts";

const execFileAsync = promisify(execFile);

/** Transient scope unit name for a limited process; deterministic from its id. */
export const scopeUnitName = (id: string): string => `tachikoma-${id}.scope`;

/**
 * Reads from a `systemd-run --user --scope` unit. Going through
 * `systemctl --user show` keeps us agnostic of where the user manager nests
 * its cgroups and works after the process has exited (the scope's cgroup
 * directory is gone by then, but systemd retains the unit's `Result` briefly).
 *
 * A seam so tests drive OOM/usage paths without a real systemd session; the
 * default implementation degrades to null/false when systemd is unavailable.
 */
export interface ScopeInspector {
  /** Live memory usage of the scope in MB; null when unknown/unavailable. */
  readMemoryCurrentMb(unit: string): Promise<number | null>;
  /** Whether the scope's process was killed by the OOM killer. */
  wasOomKilled(unit: string): Promise<boolean>;
}

const BYTES_PER_MB = 1024 * 1024;

// systemd reports an unset u64 property as this sentinel.
const U64_UNSET = "18446744073709551615";

/** Reads one property via `systemctl --user show <unit> -p <property> --value`. */
export type ShowProperty = (unit: string, property: string) => Promise<string>;

const systemctlShow: ShowProperty = async (unit, property) => {
  const { stdout } = await execFileAsync("systemctl", [
    "--user",
    "show",
    unit,
    "-p",
    property,
    "--value",
  ]);

  return stdout;
};

/** Normalize systemctl's "unset" stand-ins to null. */
const cleanValue = (raw: string): string | null => {
  const value = raw.trim();

  return value === "" || value === "[not set]" || value === U64_UNSET ? null : value;
};

export class SystemctlScopeInspector implements ScopeInspector {
  private readonly log: Logger;
  private readonly show: ShowProperty;

  constructor(log: Logger, show: ShowProperty = systemctlShow) {
    this.log = log;
    this.show = show;
  }

  async readMemoryCurrentMb(unit: string): Promise<number | null> {
    try {
      const value = cleanValue(await this.show(unit, "MemoryCurrent"));

      if (value == null) return null;

      const bytes = Number.parseInt(value, 10);

      return Number.isNaN(bytes) ? null : Math.round(bytes / BYTES_PER_MB);
    } catch (error) {
      this.log.debug({ unit, err: error }, "failed to read scope memory usage");
      return null;
    }
  }

  async wasOomKilled(unit: string): Promise<boolean> {
    try {
      return cleanValue(await this.show(unit, "Result")) === "oom-kill";
    } catch (error) {
      this.log.debug({ unit, err: error }, "failed to read scope result");
      return false;
    }
  }
}
