// pino-roll v4 ships no type declarations and there is no @types package; this
// ambient declaration covers the subset of its API we use. Return type is pino's
// DestinationStream so the stream drops directly into pino.multistream.
declare module "pino-roll" {
  import type { DestinationStream } from "pino";

  interface PinoRollLimit {
    /** Rotated files to keep, in addition to the active one. */
    count?: number;
    /** Prune matching files from prior runs too, not just this process's. */
    removeOtherLogFiles?: boolean;
  }

  interface PinoRollOptions {
    /** Base path without the date/count/extension suffix. */
    file: string | (() => string);
    /** Rotation period: "daily" | "hourly", or milliseconds. Validated at runtime. */
    frequency?: string | number;
    size?: string | number;
    extension?: string;
    dateFormat?: string;
    limit?: PinoRollLimit;
    symlink?: boolean;
    mkdir?: boolean;
  }

  export default function buildPinoRoll(options?: PinoRollOptions): Promise<DestinationStream>;
}
