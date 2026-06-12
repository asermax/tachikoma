import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskInstanceRecord } from "./schema.ts";

export interface ExpirationDeps {
  repository: TaskRepository;
  waitTimeoutSeconds: number;
  now: () => Date;
  log: Logger;
  onExpired?: (instance: TaskInstanceRecord, reason: string) => void;
}

/** Fail waiting instances whose last update exceeded the wait timeout. */
export const expireWaitingInstances = ({
  repository,
  waitTimeoutSeconds,
  now,
  log,
  onExpired,
}: ExpirationDeps): void => {
  const expired = repository.listExpiredWaitingInstances(waitTimeoutSeconds);

  for (const instance of expired) {
    const reason = `Task timed out waiting for user input after ${waitTimeoutSeconds}s`;

    repository.updateInstance(instance.id, {
      status: "failed",
      completedAt: now(),
      result: reason,
    });

    onExpired?.(instance, reason);
  }

  if (expired.length > 0) {
    log.info({ count: expired.length }, "expired waiting instances past timeout");
  }
};
