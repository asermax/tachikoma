import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskInstanceRecord } from "./schema.ts";

export interface StuckRunningDeps {
  repository: TaskRepository;
  runningTimeoutSeconds: number;
  now: () => Date;
  log: Logger;
  onStuck?: (instance: TaskInstanceRecord, reason: string) => void;
}

/**
 * Fail running instances whose start predates the running timeout. Their
 * executor is presumed dead or wedged, so failing them frees the concurrency
 * slot it was holding and surfaces the stall to the user.
 */
export const failStuckRunningInstances = ({
  repository,
  runningTimeoutSeconds,
  now,
  log,
  onStuck,
}: StuckRunningDeps): void => {
  const stuck = repository.listStuckRunningInstances(runningTimeoutSeconds);

  for (const instance of stuck) {
    const reason = `Task exceeded running timeout of ${runningTimeoutSeconds}s`;

    repository.updateInstance(instance.id, {
      status: "failed",
      completedAt: now(),
      result: reason,
    });

    onStuck?.(instance, reason);
  }

  if (stuck.length > 0) {
    log.info({ count: stuck.length }, "failed stuck running instances past timeout");
  }
};
