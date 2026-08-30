import type { DispatchBackgroundTaskPayload } from "../../events.ts";
import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";

/**
 * The DISPATCH_BACKGROUND_TASK_EVENT subscriber (DLT-080's skill-evolution reporter is the
 * first emitter): validate the payload, queue a pending ad-hoc instance, and let the existing
 * 60 s tick dispatch it. An invalid payload is logged and dropped — never thrown into the
 * emitter (task-creation logic stays here, inside tasks — DES-002). The `parseNotifyPayload`
 * posture: a plain, unit-testable function the extension's setup merely wires.
 */

/** A validated dispatch request — everything the handler needs from the payload. */
interface DispatchRequest {
  prompt: string;
  goal: string | null;
  source: string;
}

/**
 * Lenient payload validation: a non-blank string `prompt` is the only requirement. `goal`
 * normalizes to null when absent or blank (the runner can extract one later); `source`
 * defaults for the log line.
 */
export const parseDispatchPayload = (payload: unknown): DispatchRequest | null => {
  const request = payload as Partial<DispatchBackgroundTaskPayload> | null;

  if (typeof request?.prompt !== "string" || request.prompt.trim() === "") return null;

  return {
    prompt: request.prompt,
    goal: typeof request.goal === "string" && request.goal.trim() !== "" ? request.goal : null,
    source:
      typeof request.source === "string" && request.source !== "" ? request.source : "(unknown)",
  };
};

export interface DispatchDeps {
  repository: TaskRepository;
  now: () => Date;
  log: Logger;
}

/** Handle one dispatch event: create the pending background instance, or warn and drop. */
export const handleDispatchEvent = (deps: DispatchDeps, payload: unknown): void => {
  const request = parseDispatchPayload(payload);

  if (request == null) {
    deps.log.warn({ payload }, "dispatch-background-task payload has no prompt — dropped");
    return;
  }

  const instance = deps.repository.createAdHocInstance({
    taskType: "background",
    prompt: request.prompt,
    goal: request.goal,
    scheduledFor: deps.now(),
  });

  deps.log.info(
    { instanceId: instance.id, source: request.source },
    "background task instance created from dispatch event",
  );
};
