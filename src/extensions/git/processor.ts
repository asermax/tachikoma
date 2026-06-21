import { commitAll } from "../../git/commit.ts";
import type { CommitAgent } from "../../git/commit-agent.ts";
import { hasRemote, hasUncommittedChanges } from "../../git/git.ts";
import { PUSH_RESULT, PUSH_SUCCESS, type RebaseResolver, smartPush } from "../../git/sync.ts";
import type { Logger } from "../../log.ts";
import type { DebouncedTask } from "../../util/debouncer.ts";
import type { PostProcessor } from "../api.ts";

export interface GitProcessorDeps {
  workspaceRoot: string;
  agent: CommitAgent;
  resolver?: RebaseResolver;
  /** Cleared and drained at trunk close so the finalize pass owns persistence exclusively. */
  debouncer?: DebouncedTask;
}

export const workspaceFallbackMessage = (now = new Date()): string =>
  `Update workspace files (${now.toISOString().slice(0, 10)})`;

export interface CommitAndPushWorkspaceDeps {
  workspaceRoot: string;
  agent: CommitAgent;
  resolver?: RebaseResolver;
  log: Logger;
}

/**
 * Commit every workspace change via the agent-driven grouped flow (falling back to a
 * single deterministic commit on failure), then push to `origin` when a remote is
 * configured. Shared by the finalize-phase trunk-close processor and the debounced
 * per-exchange fire so both follow the same commit-then-push path.
 */
export const commitAndPushWorkspace = async ({
  workspaceRoot,
  agent,
  resolver,
  log,
}: CommitAndPushWorkspaceDeps): Promise<void> => {
  if (!(await hasUncommittedChanges(workspaceRoot))) {
    log.debug("workspace is clean — no commits needed");
    return;
  }

  const subjects = await commitAll({
    agent,
    cwd: workspaceRoot,
    fallbackMessage: workspaceFallbackMessage(),
    log,
  });

  if (subjects.length > 0) log.info({ subjects }, "committed workspace changes");

  if (await hasRemote(workspaceRoot, "origin")) {
    const result = await smartPush(workspaceRoot, "origin", "HEAD", log, resolver);

    if (PUSH_SUCCESS.has(result)) {
      log.info({ result }, "pushed workspace changes");
    } else if (result === PUSH_RESULT.nothingToPush) {
      log.debug("nothing to push");
    } else {
      log.warn({ result }, "push failed — changes remain committed locally");
    }
  }

  if (await hasUncommittedChanges(workspaceRoot)) {
    log.warn("uncommitted changes remain after commit pass — retrying");

    await commitAll({
      agent,
      cwd: workspaceRoot,
      fallbackMessage: workspaceFallbackMessage(),
      log,
    });

    if (await hasUncommittedChanges(workspaceRoot)) {
      log.warn("uncommitted changes remain after git processor retry");
    }
  }
};

/**
 * Finalize-phase post-processor: the trunk-close backstop for workspace versioning.
 * Clears and drains the debounced per-exchange commit-push first (so the finalize
 * pass owns persistence exclusively and never races a pending fire), then runs the
 * shared commit-and-push.
 */
export const createGitProcessor = ({
  workspaceRoot,
  agent,
  resolver,
  debouncer,
}: GitProcessorDeps): PostProcessor => ({
  name: "git-commit",
  phase: "finalize",

  async process({ log }) {
    debouncer?.clear();
    await debouncer?.whenIdle();
    await commitAndPushWorkspace({ workspaceRoot, agent, resolver, log });
  },
});
