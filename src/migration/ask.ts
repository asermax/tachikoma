import { createInterface } from "node:readline/promises";

import type { Logger } from "../log.ts";

export type Ask = (question: string) => Promise<boolean>;

/**
 * Yes/no startup prompt. Interactive only on a TTY; otherwise every question
 * resolves to its safe default (no) with a warning. Prompts run before any
 * channel starts, so there is no stdin contention with a terminal channel.
 */
export const createAsk = (log: Logger): Ask => {
  return async (question) => {
    if (process.stdin.isTTY !== true) {
      log.warn({ question }, "non-interactive startup — answering no");
      return false;
    }

    // Prompt on stderr so any channel writing to stdout stays clean.
    const readline = createInterface({ input: process.stdin, output: process.stderr });

    try {
      const answer = await readline.question(`${question} [y/N] `);
      return /^y(es)?$/i.test(answer.trim());
    } finally {
      readline.close();
    }
  };
};
