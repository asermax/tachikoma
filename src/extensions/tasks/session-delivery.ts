import type { Delivery } from "../../channels/types.ts";
import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskDefinitionRecord, TaskInstanceRecord } from "./schema.ts";

export interface SessionDeliveryDeps {
  repository: TaskRepository;
  deliver: (delivery: Delivery) => void;
  now: () => Date;
  log: Logger;
}

export const renderSessionTaskText = (
  instance: TaskInstanceRecord,
  definition: TaskDefinitionRecord | null,
): string => {
  const label = definition != null ? `Scheduled task: ${definition.name}` : "Scheduled task";

  return `📋 ${label}\n\n${instance.prompt}`;
};

/**
 * One delivery pass: route pending session instances to the user through the
 * idle-gated channel delivery, walking the pending → running → completed
 * lifecycle. A failed handoff rolls the instance back to pending for retry.
 */
export const deliverSessionTasks = ({
  repository,
  deliver,
  now,
  log,
}: SessionDeliveryDeps): void => {
  for (const instance of repository.getPendingInstances("session")) {
    const definition =
      instance.definitionId != null ? repository.getDefinition(instance.definitionId) : null;

    repository.updateInstance(instance.id, { status: "running", startedAt: now() });

    try {
      deliver({
        // The agent acts on the task inside the session; the user sees its response.
        text: renderSessionTaskText(instance, definition),
        tier: "normal",
        metadata: { kind: "session_task", instanceId: instance.id },
      });
    } catch (error) {
      // Roll back so the next tick retries — otherwise the row is stuck
      // running with no delivery attached.
      repository.updateInstance(instance.id, { status: "pending", startedAt: null });
      log.error({ instanceId: instance.id, err: error }, "session task delivery failed");
      continue;
    }

    repository.updateInstance(instance.id, {
      status: "completed",
      completedAt: now(),
      result: "Delivered successfully",
    });

    log.info({ instanceId: instance.id }, "session task delivered");
  }
};
