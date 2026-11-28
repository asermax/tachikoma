/**
 * Restart fixture for `tests/self-update/seams.test.ts`: runs under plain node,
 * records its identity on every boot, and replaces itself through the real
 * `ProcessRestarter` on its first boot. vitest does not collect it (only
 * `*.test.ts` files are) and nothing may import it — it executes on load.
 *
 * Which boot this is comes from the record file, never from the environment: the
 * predecessor's environment is the successor's environment too. See {@link RESTART_BOOT}
 * for the variables that drive it.
 */
import { randomBytes } from "node:crypto";
import { appendFileSync, existsSync } from "node:fs";

import { ProcessRestarter } from "../../../src/extensions/self-update/seams.ts";
import { acquireInstanceLock } from "../../../src/instance-lock.ts";
import type { Logger } from "../../../src/log.ts";
import { type BootRecord, RESTART_BOOT } from "../setup.ts";

const env = process.env;
const recordPath = env[RESTART_BOOT.record];
if (recordPath == null) throw new Error(`${RESTART_BOOT.record} is required`);

const record = (entry: BootRecord): void => {
  appendFileSync(recordPath, `${JSON.stringify(entry)}\n`);
};

const identity = () => ({
  pid: process.pid,
  ppid: process.ppid,
  argv: process.argv.slice(2),
});

// Both boots take the workspace lock when asked, as runApp does at boot: the predecessor
// acquires it, the successor — which kept the pid — reclaims it. A refusal throws, and
// node exits non-zero with the error on stderr.
const lockPath = env[RESTART_BOOT.lock];
if (lockPath != null) await acquireInstanceLock(lockPath);

if (existsSync(recordPath)) {
  // The successor: report what it found.
  record({
    ...identity(),
    carried: env[RESTART_BOOT.carried] ?? null,
    lock: lockPath != null ? "acquired" : "not-held",
  });

  await new Promise<void>((resolve) => {
    process.stdout.write(`${RESTART_BOOT.stdoutLine}\n`, () => resolve());
  });
  process.exit(Number(env[RESTART_BOOT.exitCode] ?? 0));
}

// The predecessor: assign the variable the successor must find, then re-exec.
const assigned = randomBytes(8).toString("hex");
record({ ...identity(), assigned });
process.env[RESTART_BOOT.carried] = assigned;

new ProcessRestarter({ warn: () => {} } as unknown as Logger).restart();

// Not reached while the restarter works: this exit code says the process was never replaced.
process.exit(99);
