import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";

export interface OneShotCleanupDeps {
  repository: TaskRepository;
  retentionSeconds: number;
  log: Logger;
}

/**
 * Retention pass: prune auto-disabled one-shot definitions (and their terminal
 * instances) once they age past the retention window, so spent one-shots don't
 * accumulate indefinitely.
 */
export const cleanupExpiredOneShots = ({
  repository,
  retentionSeconds,
  log,
}: OneShotCleanupDeps): void => {
  const deleted = repository.pruneExpiredOneShotDefinitions(retentionSeconds);

  if (deleted > 0) {
    log.info({ count: deleted }, "pruned expired one-shot definitions past retention");
  }
};
