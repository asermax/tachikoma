import { commitAll } from "../../git/commit.ts";
import type { CommitAgent } from "../../git/commit-agent.ts";
import { hasRemote, hasUncommittedChanges } from "../../git/git.ts";
import { PUSH_RESULT, PUSH_SUCCESS, type RebaseResolver, smartPush } from "../../git/sync.ts";
import type { PostProcessor } from "../api.ts";

export interface GitProcessorDeps {
  workspaceRoot: string;
  agent: CommitAgent;
  resolver?: RebaseResolver;
}

export const workspaceFallbackMessage = (now = new Date()): string =>
  `Update workspace files (${now.toISOString().slice(0, 10)})`;

/**
 * Finalize-phase post-processor: commits all workspace changes after each
 * session via the agent-driven grouped flow (falling back to a single
 * deterministic commit on failure), then pushes to origin when a remote is
 * configured.
 */
export const createGitProcessor = ({
  workspaceRoot,
  agent,
  resolver,
}: GitProcessorDeps): PostProcessor => ({
  name: "git-commit",
  phase: "finalize",

  async process({ log }) {
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
  },
});
