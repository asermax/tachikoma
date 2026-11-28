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
  /** Workspace lock both boots take, as runApp does at boot (optional). */
  lock: "TACHIKOMA_TEST_LOCK",
  /** The successor's exit code (default 0). */
  exitCode: "TACHIKOMA_TEST_EXIT_CODE",
  /** Environment variable the predecessor assigns at run time. */
  carried: "TACHIKOMA_TEST_CARRIED",
  /** Line the successor prints to its inherited stdout. */
  stdoutLine: "successor on stdout",
} as const;

/** What one boot of the restart fixture observed. */
export interface BootRecord {
  pid: number;
  ppid: number;
  /** Arguments after the script path. */
  argv: string[];
  /** Predecessor only: the value it assigned to the carried environment variable. */
  assigned?: string;
  /** Successor only: the carried variable as it found it. */
  carried?: string | null;
  /** Successor only: whether it holds the workspace lock. */
  lock?: "acquired" | "not-held";
}
