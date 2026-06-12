import type { PostProcessor } from "../api.ts";
import type { WorkflowStateRepository } from "./repository.ts";
import { deleteScratchpad } from "./tools.ts";

export const DEFAULT_STALE_HOURS = 24;

export type StaleRepository = Pick<WorkflowStateRepository, "listStale" | "softDelete">;

/**
 * Post-processor that expires workflow instances abandoned across sessions:
 * soft-deletes records untouched for longer than the threshold and removes
 * their scratchpad files.
 */
export const createStaleWorkflowCleanup = (
  repository: StaleRepository,
  staleHours: number = DEFAULT_STALE_HOURS,
): PostProcessor => ({
  name: "stale-workflow-cleanup",
  phase: "main",

  async process({ log }) {
    let stale: ReturnType<StaleRepository["listStale"]>;

    try {
      stale = repository.listStale(staleHours * 60 * 60 * 1000);
    } catch (error) {
      log.error({ err: error }, "failed to list stale workflows");
      return;
    }

    if (stale.length === 0) return;

    let cleaned = 0;

    for (const state of stale) {
      try {
        if (repository.softDelete(state.id)) {
          deleteScratchpad(state.scratchpadPath);
          cleaned += 1;
        }
      } catch (error) {
        log.warn({ err: error, workflowId: state.id }, "failed to clean up stale workflow");
      }
    }

    if (cleaned > 0) log.info({ cleaned, staleHours }, "cleaned up stale workflow(s)");
  },
});
