import pino from "pino";
import pinoPretty from "pino-pretty";
import buildPinoRoll from "pino-roll";

export type Logger = ReturnType<typeof pino>;

export interface FileSink {
  /** Base path WITHOUT extension, e.g. {logsDir}/tachikoma. */
  path: string;
  /** "daily" | "hourly" from config, or milliseconds (tests). */
  frequency: string | number;
  /** pino-roll limit.count — derived from retentionDays by the caller. */
  retainedFiles: number;
}

export interface LogOptions {
  level: string;
  pretty: boolean;
  /** When set, lines are written to a rotating file *and* to stderr. */
  file?: FileSink;
}

// pino-roll prunes by file count; convert "days" → number of files for the period.
// Frequencies match pino-roll's supported set; the ?? 1 only guards a direct call
// with an unmapped value (config validation rejects those before they reach here).
const FILES_PER_DAY: Record<string, number> = { hourly: 24, daily: 1 };

export const retainedFiles = (retentionDays: number, frequency: string): number =>
  Math.max(1, Math.ceil(retentionDays * (FILES_PER_DAY[frequency] ?? 1)));

// Logs always go to stderr so channel output on stdout (REPL) stays clean.
export const createRootLogger = async ({ level, pretty, file }: LogOptions): Promise<Logger> => {
  // Legacy configs used uppercase levels ("INFO"); pino requires lowercase,
  // and this runs before config migration gets a chance to translate.
  const normalized = level.toLowerCase();

  // pino-pretty is wired as a stream (not a transport) so it composes with
  // multistream — transports run in a worker thread and can't be combined.
  const stderr = pretty ? pinoPretty({ destination: 2, colorize: true }) : pino.destination(2);

  if (file == null) return pino({ level: normalized }, stderr);

  // pino-roll is an in-process SonicBoom stream (not a worker-thread transport),
  // so it composes with multistream exactly like the stderr stream above.
  const fileStream = await buildPinoRoll({
    file: file.path,
    extension: ".log",
    dateFormat: "yyyy-MM-dd",
    frequency: file.frequency,
    // removeOtherLogFiles keeps retention honest across restarts: pino-roll
    // otherwise only prunes files the current process created.
    limit: { count: file.retainedFiles, removeOtherLogFiles: true },
    mkdir: true,
    symlink: true,
  });

  return pino(
    { level: normalized },
    pino.multistream([{ stream: stderr }, { stream: fileStream }]),
  );
};

export const componentLogger = (root: Logger, component: string): Logger =>
  root.child({ component });
