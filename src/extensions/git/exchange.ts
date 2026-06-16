import { commitAll } from "../../git/commit.ts";
import type { CommitAgent } from "../../git/commit-agent.ts";
import { hasUncommittedChanges } from "../../git/git.ts";
import type { Logger } from "../../log.ts";
import type { ExchangeProcessor } from "../api.ts";
import { workspaceFallbackMessage } from "./processor.ts";

export interface GitExchangeProcessorDeps {
  workspaceRoot: string;
  agent: CommitAgent;
  log: Logger;
}

export interface GitExchangeProcessor extends ExchangeProcessor {
  /** Resolves once no per-exchange commit is in flight (test seam / orderly shutdown). */
  whenIdle(): Promise<void>;
}

/**
 * Per-exchange safety commit: after each exchange, group any uncommitted
 * workspace changes into a cohesive agent-driven commit so work is durable
 * mid-day with a meaningful message. The agent runs a model call, so the commit
 * is launched fire-and-forget — it never blocks the exchange — and single-flighted:
 * while one commit runs, later exchanges skip and the next one sweeps up whatever
 * is left. The push stays at trunk close (see `createGitProcessor`).
 */
export const createGitExchangeProcessor = ({
  workspaceRoot,
  agent,
  log,
}: GitExchangeProcessorDeps): GitExchangeProcessor => {
  let inFlight: Promise<void> | null = null;

  return {
    name: "git-exchange-commit",

    async process() {
      if (inFlight != null) return;
      if (!(await hasUncommittedChanges(workspaceRoot))) return;

      inFlight = commitAll({
        agent,
        cwd: workspaceRoot,
        fallbackMessage: workspaceFallbackMessage(),
        log,
      })
        .then((subjects) => {
          if (subjects.length > 0) log.debug({ subjects }, "per-exchange workspace commit");
        })
        .catch((err) => log.error({ err }, "per-exchange workspace commit failed"))
        .finally(() => {
          inFlight = null;
        });
    },

    async whenIdle() {
      await inFlight;
    },
  };
};
