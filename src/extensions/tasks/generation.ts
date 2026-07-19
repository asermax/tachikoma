import type { Logger } from "../../log.ts";
import type { TaskRepository } from "./repository.ts";
import { nextCronRun } from "./schedule.ts";
import type { TaskDefinitionRecord } from "./schema.ts";

export interface GenerationDeps {
  repository: TaskRepository;
  timezone: string | undefined;
  now: () => Date;
  log: Logger;
}

const HOUR_MS = 3_600_000;

/**
 * One generation pass: evaluate every enabled definition against the current
 * time, create pending instances for due schedules, and advance bookkeeping.
 * One-shot definitions are auto-disabled after firing.
 */
export const generateDueInstances = (deps: GenerationDeps): void => {
  for (const definition of deps.repository.listEnabledDefinitions()) {
    try {
      if (definition.schedule.type === "cron") {
        generateForCron(deps, definition, definition.schedule.expression);
      } else {
        generateForOneShot(deps, definition, new Date(definition.schedule.at));
      }
    } catch (error) {
      deps.log.error({ definitionId: definition.id, err: error }, "error processing definition");
    }
  }
};

const generateForCron = (
  { repository, timezone, now, log }: GenerationDeps,
  definition: TaskDefinitionRecord,
  expression: string,
): void => {
  const current = now();

  // First evaluation looks back to the start of the current hour (minus a second
  // so an exact on-the-hour match still fires); afterwards the last fire anchors.
  const anchor =
    definition.lastFiredAt ?? new Date(Math.floor(current.getTime() / HOUR_MS) * HOUR_MS - 1000);

  let nextFire = nextCronRun(expression, timezone, anchor);

  // Stale-cron prevention: never fire for occurrences that predate the
  // definition's latest edit — advance past `since` instead.
  if (nextFire != null && nextFire <= definition.since) {
    nextFire = nextCronRun(expression, timezone, definition.since);
  }

  if (nextFire == null || nextFire > current) return;

  const duplicate = repository.getActiveInstanceForDefinition(definition.id, nextFire);

  if (duplicate != null) {
    log.debug(
      { definitionId: definition.id, scheduledFor: nextFire.toISOString() },
      "duplicate suppressed — period already covered",
    );
    return;
  }

  const instance = repository.createInstance({
    definitionId: definition.id,
    taskType: definition.taskType,
    prompt: definition.prompt,
    goal: definition.goal,
    scheduledFor: nextFire,
  });

  // Advance lastFiredAt so the next pass anchors in the future, preventing
  // catch-up duplicates for the same period.
  repository.updateDefinition(definition.id, { lastFiredAt: current });

  log.info(
    { instanceId: instance.id, name: definition.name, taskType: definition.taskType },
    "created task instance",
  );
};

const generateForOneShot = (
  { repository, now, log }: GenerationDeps,
  definition: TaskDefinitionRecord,
  at: Date,
): void => {
  const current = now();

  if (definition.lastFiredAt != null || at > current) return;

  const active = repository.getActiveInstanceForDefinition(definition.id);

  if (active != null) {
    log.debug(
      { definitionId: definition.id, instanceId: active.id },
      "skipping one-shot — already has an active instance",
    );
    return;
  }

  const instance = repository.createInstance({
    definitionId: definition.id,
    taskType: definition.taskType,
    prompt: definition.prompt,
    goal: definition.goal,
    scheduledFor: at,
  });

  repository.updateDefinition(definition.id, { lastFiredAt: current, enabled: false });

  log.info(
    { instanceId: instance.id, name: definition.name, taskType: definition.taskType },
    "created one-shot instance and auto-disabled its definition",
  );
};
