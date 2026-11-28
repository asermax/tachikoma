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
import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";

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

/**
 * Process group from `/proc/self/stat`; null where `/proc` is absent. An exec never
 * changes it, so the pid equality already carries the guarantee.
 */
const pgid = (): number | null => {
  try {
    const raw = readFileSync("/proc/self/stat", "utf8");
    return Number(raw.slice(raw.lastIndexOf(")") + 2).split(/\s+/)[2]);
  } catch {
    return null;
  }
};

const identity = () => ({
  pid: process.pid,
  ppid: process.ppid,
  pgid: pgid(),
  argv: process.argv.slice(2),
});

const bootsSoFar = existsSync(recordPath)
  ? readFileSync(recordPath, "utf8")
      .split("\n")
      .filter((line) => line !== "").length
  : 0;

if (bootsSoFar >= 1) {
  // The successor: report what it found, then hold until the test releases it if asked.
  let lock = "not-held";
  let lockRefused = false;
  if (env[RESTART_BOOT.lock] != null) {
    try {
      await acquireInstanceLock(env[RESTART_BOOT.lock] as string);
      lock = "acquired";
    } catch (error) {
      lockRefused = true;
      lock = `refused: ${error instanceof Error ? error.message.split("\n")[0] : error}`;
    }
  }

  record({
    boot: bootsSoFar,
    ...identity(),
    carried: env[RESTART_BOOT.carried] ?? null,
    lock,
  });

  if (env[RESTART_BOOT.ready] != null) {
    await new Promise<void>((resolve) => {
      process.stdout.write(`${RESTART_BOOT.stdoutLine}\n`, () => resolve());
    });
    writeFileSync(env[RESTART_BOOT.ready] as string, "ready");
  }

  const release = env[RESTART_BOOT.release];
  const deadline = Date.now() + 15_000;
  while (release != null && !existsSync(release)) {
    // The test never released the successor: fail instead of hanging.
    if (Date.now() > deadline) process.exit(4);
    await delay(20);
  }

  process.exit(lockRefused ? 3 : Number(env[RESTART_BOOT.exitCode] ?? 0));
}

// The predecessor: hold the workspace lock if asked to (as runApp does at boot), then re-exec.
if (env[RESTART_BOOT.lock] != null) await acquireInstanceLock(env[RESTART_BOOT.lock] as string);

const assigned = randomBytes(8).toString("hex");
record({ boot: 0, ...identity(), assigned });
process.env[RESTART_BOOT.carried] = assigned;

new ProcessRestarter({ warn: () => {} } as unknown as Logger).restart();

// Not reached while the restarter works: a record from here means the process was never replaced.
record({ boot: 0, ...identity(), unreachable: true });
process.exit(99);
