import { readdir, rename, stat, unlink } from "node:fs/promises";
import { join } from "node:path";
import pino from "pino";
import pinoPretty from "pino-pretty";

export type Logger = ReturnType<typeof pino>;

export interface LogOptions {
  level: string;
  pretty: boolean;
  /** Absolute path to a JSON log file; when set, lines are written there *and* to stderr. */
  file?: string;
}

// Logs always go to stderr so channel output on stdout (REPL) stays clean.
export const createRootLogger = ({ level, pretty, file }: LogOptions): Logger => {
  // Legacy configs used uppercase levels ("INFO"); pino requires lowercase,
  // and this runs before config migration gets a chance to translate.
  const normalized = level.toLowerCase();

  // pino-pretty is wired as a stream (not a transport) so it composes with
  // multistream — transports run in a worker thread and can't be combined.
  const stderr = pretty ? pinoPretty({ destination: 2, colorize: true }) : pino.destination(2);

  if (file == null) return pino({ level: normalized }, stderr);

  return pino(
    { level: normalized },
    pino.multistream([
      { stream: stderr },
      { stream: pino.destination({ dest: file, mkdir: true }) },
    ]),
  );
};

export const componentLogger = (root: Logger, component: string): Logger =>
  root.child({ component });

const LOG_BASENAME = "tachikoma.log";
const ROTATED_PATTERN = /^tachikoma\.\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.log$/;

const rotationStamp = (now: Date): string => {
  const pad = (n: number) => String(n).padStart(2, "0");

  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
};

/**
 * Startup rotation: archive the current log under a timestamped name and prune
 * archives older than the retention window. Self-contained so daemon runs don't
 * pull in an external rotation dependency.
 */
export const rotateLogs = async (
  logsDir: string,
  retentionDays: number,
  now: Date = new Date(),
): Promise<void> => {
  const current = join(logsDir, LOG_BASENAME);

  try {
    await rename(current, join(logsDir, `tachikoma.${rotationStamp(now)}.log`));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }

  const cutoff = now.getTime() - retentionDays * 24 * 60 * 60 * 1000;

  let entries: string[];
  try {
    entries = await readdir(logsDir);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return;
    throw err;
  }

  for (const entry of entries) {
    if (!ROTATED_PATTERN.test(entry)) continue;

    const path = join(logsDir, entry);
    if ((await stat(path)).mtimeMs < cutoff) await unlink(path);
  }
};
