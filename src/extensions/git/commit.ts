import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import { runGit, runGitCapture } from "./git.ts";

export type Completer = Pick<SideRunner, "complete">;

const COMMIT_MESSAGE_SYSTEM = `You write git commit messages for an automated workspace-versioning agent.

Given the staged diffstat of a commit, respond with a single descriptive commit
message line: imperative mood, under 72 characters, mentioning what changed.
Output only the message — no quotes, no prose, no trailing punctuation.`;

const MAX_MESSAGE_CHARS = 100;

const sanitizeMessage = (raw: string): string | null => {
  const line = raw
    .split("\n")
    .map((candidate) => candidate.trim())
    .find((candidate) => candidate !== "");

  if (line == null) return null;

  const cleaned = line.replaceAll(/^["'`]+|["'`]+$/g, "").trim();

  if (cleaned === "") return null;

  return cleaned.length <= MAX_MESSAGE_CHARS ? cleaned : `${cleaned.slice(0, MAX_MESSAGE_CHARS)}…`;
};

export const generateCommitMessage = async (
  side: Completer,
  diffStat: string,
  fallback: string,
  log: Logger,
): Promise<string> => {
  try {
    const generated = sanitizeMessage(
      await side.complete({ system: COMMIT_MESSAGE_SYSTEM, user: diffStat, tier: "processor" }),
    );

    return generated ?? fallback;
  } catch (error) {
    log.warn({ err: error }, "commit message generation failed — using fallback");
    return fallback;
  }
};

export interface CommitAllOptions {
  cwd: string;
  /** Generates the message from the diffstat; omit when passing an explicit message. */
  side?: Completer;
  /** Deterministic message used when generation fails or produces nothing usable. */
  fallbackMessage: string;
  /** Explicit message — skips generation entirely. */
  message?: string;
  log: Logger;
}

/**
 * Stage everything and commit with a descriptive message generated from the
 * staged diffstat. Returns the commit message, or null when there was nothing
 * to commit.
 */
export const commitAll = async ({
  cwd,
  side,
  fallbackMessage,
  message,
  log,
}: CommitAllOptions): Promise<string | null> => {
  await runGit(cwd, ["add", "-A"]);

  const { stdout: diffStat } = await runGitCapture(cwd, ["diff", "--cached", "--stat"]);

  if (diffStat === "") return null;

  const resolved =
    message ??
    (side != null
      ? await generateCommitMessage(side, diffStat, fallbackMessage, log)
      : fallbackMessage);
  await runGit(cwd, ["commit", "-m", resolved]);

  return resolved;
};
