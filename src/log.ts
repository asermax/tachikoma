import pino from "pino";

export type Logger = ReturnType<typeof pino>;

export interface LogOptions {
  level: string;
  pretty: boolean;
}

// Logs always go to stderr so channel output on stdout (REPL) stays clean.
export const createRootLogger = ({ level, pretty }: LogOptions): Logger => {
  // Python-era configs used uppercase levels ("INFO"); pino requires lowercase,
  // and this runs before config migration gets a chance to translate.
  const normalized = level.toLowerCase();

  return pretty
    ? pino({
        level: normalized,
        transport: { target: "pino-pretty", options: { destination: 2, colorize: true } },
      })
    : pino({ level: normalized }, pino.destination(2));
};

export const componentLogger = (root: Logger, component: string): Logger =>
  root.child({ component });
