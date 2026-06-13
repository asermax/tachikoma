import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Logger } from "../../log.ts";
import { scopeUnitName } from "./cgroup.ts";

const execFileAsync = promisify(execFile);

export interface WrappedCommand {
  file: string;
  args: string[];
  /** Whether a memory limit is actually enforced on the spawned process. */
  limited: boolean;
}

/**
 * Seam for resource limiting; a direct cgroup v2 implementation can slot in
 * behind this interface.
 */
export interface ProcessLimiter {
  wrap(id: string, command: string, memoryLimitMb: number | null): WrappedCommand;
}

const plainShell = (command: string): WrappedCommand => ({
  file: "sh",
  args: ["-c", command],
  limited: false,
});

/**
 * Limits via `systemd-run --user --scope -p MemoryMax=…`: systemd places the
 * command in a transient scope (cgroup) and exits with the command's status,
 * so liveness checks and exit codes behave like an unwrapped spawn.
 */
export class SystemdRunLimiter implements ProcessLimiter {
  private available = false;
  private readonly log: Logger;

  constructor(log: Logger) {
    this.log = log;
  }

  /** Probe `systemd-run` once at bootstrap; wrap() degrades gracefully without it. */
  async detect(
    probe: () => Promise<unknown> = () => execFileAsync("systemd-run", ["--version"]),
  ): Promise<void> {
    try {
      await probe();
      this.available = true;
    } catch {
      this.available = false;
      this.log.info("systemd-run not available — processes will run without memory limits");
    }
  }

  wrap(id: string, command: string, memoryLimitMb: number | null): WrappedCommand {
    if (memoryLimitMb == null) return plainShell(command);

    if (!this.available) {
      this.log.warn({ memoryLimitMb }, "systemd-run unavailable — spawning without memory limit");
      return plainShell(command);
    }

    // Naming the scope makes it addressable for live usage reads and OOM
    // attribution via `systemctl --user show <unit>` (see cgroup.ts).
    return {
      file: "systemd-run",
      args: [
        "--user",
        "--scope",
        "--quiet",
        `--unit=${scopeUnitName(id)}`,
        "-p",
        `MemoryMax=${memoryLimitMb}M`,
        "--",
        "sh",
        "-c",
        command,
      ],
      limited: true,
    };
  }
}
