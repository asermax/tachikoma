import { join } from "node:path";

/**
 * Shared contract of the restart fixture (`tests/self-update/fixtures/restart-boot.ts`):
 * a script the tests run under plain node, which replaces itself through the real
 * `ProcessRestarter` and reports what each boot observed.
 */
export const RESTART_BOOT = {
  script: join(import.meta.dirname, "fixtures", "restart-boot.ts"),
  /** Append-only JSON-lines file with one {@link BootRecord} per boot (required). */
  record: "TACHIKOMA_TEST_RECORD",
  /** Workspace lock the predecessor holds across the restart (optional). */
  lock: "TACHIKOMA_TEST_LOCK",
  /** File the successor creates once it is up (optional). */
  ready: "TACHIKOMA_TEST_READY",
  /** File the successor waits for before it exits (optional). */
  release: "TACHIKOMA_TEST_RELEASE",
  /** The successor's exit code (default 0). */
  exitCode: "TACHIKOMA_TEST_EXIT_CODE",
  /** Environment variable the predecessor assigns at run time. */
  carried: "TACHIKOMA_TEST_CARRIED",
  /** Line the successor prints to its inherited stdout. */
  stdoutLine: "successor on stdout",
} as const;

/** What one boot of the restart fixture observed. */
export interface BootRecord {
  /** Zero for the predecessor, one for its successor. */
  boot: number;
  pid: number;
  ppid: number;
  /** Process group from `/proc/self/stat`; null where `/proc` is absent. */
  pgid: number | null;
  /** Arguments after the script path. */
  argv: string[];
  /** Predecessor only: the value it assigned to the carried environment variable. */
  assigned?: string;
  /** Successor only: the carried variable as it found it. */
  carried?: string | null;
  /** Successor only: whether it could take the lock its predecessor held. */
  lock?: string;
  /** Set when the restarter returned instead of replacing the process. */
  unreachable?: true;
}
