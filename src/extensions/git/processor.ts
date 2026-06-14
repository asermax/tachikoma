import { type Completer, commitAll } from "../../git/commit.ts";
import { hasRemote, hasUncommittedChanges } from "../../git/git.ts";
import { PUSH_RESULT, PUSH_SUCCESS, type RebaseResolver, smartPush } from "../../git/sync.ts";
import type { PostProcessor } from "../api.ts";

export interface GitProcessorDeps {
  workspaceRoot: string;
  side: Completer;
  resolver?: RebaseResolver;
}

export const workspaceFallbackMessage = (now = new Date()): string =>
  `Update workspace files (${now.toISOString().slice(0, 10)})`;

/**
 * Finalize-phase post-processor: stages and commits all workspace changes after
 * each session with a message generated from the staged diffstat, then pushes
 * to origin when a remote is configured.
 */
export const createGitProcessor = ({
  workspaceRoot,
  side,
  resolver,
}: GitProcessorDeps): PostProcessor => ({
  name: "git-commit",
  phase: "finalize",

  async process({ log }) {
    if (!(await hasUncommittedChanges(workspaceRoot))) {
      log.debug("workspace is clean — no commits needed");
      return;
    }

    const message = await commitAll({
      cwd: workspaceRoot,
      side,
      fallbackMessage: workspaceFallbackMessage(),
      log,
    });

    if (message != null) log.info({ message }, "committed workspace changes");

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
        cwd: workspaceRoot,
        side,
        fallbackMessage: workspaceFallbackMessage(),
        log,
      });

      if (await hasUncommittedChanges(workspaceRoot)) {
        log.warn("uncommitted changes remain after git processor retry");
      }
    }
  },
});
