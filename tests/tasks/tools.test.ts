import { beforeEach, describe, expect, it } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import {
  handleCreateTask,
  handleListTasks,
  handleQueryTaskInstances,
  handleUpdateTask,
  type ToolDeps,
} from "../../src/extensions/tasks/tools.ts";
import { createTasksTestDb } from "./setup.ts";

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;
let deps: ToolDeps;

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, () => current);
  deps = { repository, timezone: "UTC", now: () => current };
});

describe("handleCreateTask", () => {
  it("creates a cron task definition", () => {
    const message = handleCreateTask(deps, {
      name: "daily briefing",
      schedule: "0 9 * * *",
      type: "session",
      prompt: "brief me",
    });

    expect(message).toContain("Task 'daily briefing' created successfully.");
    expect(message).toContain("Schedule: cron: 0 9 * * *");

    const definitions = repository.listEnabledDefinitions();
    expect(definitions).toHaveLength(1);
    expect(definitions[0]).toMatchObject({
      taskType: "session",
      prompt: "brief me",
      enabled: true,
      schedule: { type: "cron", expression: "0 9 * * *" },
    });
  });

  it("creates a one-shot from a bare datetime interpreted in the configured timezone", () => {
    handleCreateTask(deps, {
      name: "one off",
      schedule: "2026-07-01T15:00:00",
      type: "background",
      prompt: "do it once",
    });

    expect(repository.listEnabledDefinitions()[0]?.schedule).toEqual({
      type: "once",
      at: "2026-07-01T15:00:00.000Z",
    });
  });

  it("rejects an invalid schedule", () => {
    expect(() =>
      handleCreateTask(deps, {
        name: "broken",
        schedule: "whenever you feel like it",
        type: "session",
        prompt: "nope",
      }),
    ).toThrow(/Invalid schedule/);
  });

  it("rejects a one-shot datetime in the past", () => {
    expect(() =>
      handleCreateTask(deps, {
        name: "too late",
        schedule: "2026-06-12T09:00:00Z",
        type: "session",
        prompt: "nope",
      }),
    ).toThrow(/must be in the future/);
  });
});

describe("handleUpdateTask", () => {
  it("updates provided fields only", () => {
    const definition = repository.createDefinition({
      name: "old name",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "old prompt",
    });

    const message = handleUpdateTask(deps, {
      task_id: definition.id,
      name: "new name",
      enabled: false,
    });

    expect(message).toBe(`Task '${definition.id}' updated successfully.`);
    expect(repository.getDefinition(definition.id)).toMatchObject({
      name: "new name",
      enabled: false,
      prompt: "old prompt",
    });
  });

  it("resets lastFiredAt when the schedule changes", () => {
    const definition = repository.createDefinition({
      name: "task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });
    repository.updateDefinition(definition.id, { lastFiredAt: current });

    handleUpdateTask(deps, { task_id: definition.id, schedule: "0 18 * * *" });

    const updated = repository.getDefinition(definition.id);
    expect(updated?.lastFiredAt).toBeNull();
    expect(updated?.schedule).toEqual({ type: "cron", expression: "0 18 * * *" });
  });

  it("throws for an unknown task id", () => {
    expect(() => handleUpdateTask(deps, { task_id: "missing" })).toThrow(
      "Task 'missing' not found.",
    );
  });

  it("reports when no updates were provided", () => {
    const definition = repository.createDefinition({
      name: "task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });

    expect(handleUpdateTask(deps, { task_id: definition.id })).toBe("No updates provided.");
  });
});

describe("handleListTasks", () => {
  it("lists active definitions by default and archived on request", () => {
    repository.createDefinition({
      name: "active task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });
    repository.createDefinition({
      name: "archived task",
      schedule: { type: "cron", expression: "0 10 * * *" },
      taskType: "background",
      prompt: "prompt",
      enabled: false,
    });

    const active = handleListTasks(deps, {});
    expect(active).toContain("active task");
    expect(active).not.toContain("archived task");

    const archived = handleListTasks(deps, { archived: true });
    expect(archived).toContain("archived task");
    expect(archived).not.toContain("active task");
  });

  it("reports empty lists", () => {
    expect(handleListTasks(deps, {})).toBe("No active tasks found.");
    expect(handleListTasks(deps, { archived: true })).toBe("No archived tasks found.");
  });
});

describe("handleQueryTaskInstances", () => {
  it("filters instances by status and definition", () => {
    const definition = repository.createDefinition({
      name: "task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "background",
      prompt: "prompt",
    });
    const done = repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: "prompt",
      scheduledFor: current,
    });
    repository.updateInstance(done.id, { status: "completed", result: "all good" });
    repository.createInstance({
      definitionId: null,
      taskType: "session",
      prompt: "other",
      scheduledFor: current,
    });

    const completed = handleQueryTaskInstances(deps, { status: "completed" });
    expect(completed).toContain(done.id);
    expect(completed).toContain("Result: all good");
    expect(completed).toContain(`Definition: ${definition.id}`);

    const pending = handleQueryTaskInstances(deps, { status: "pending" });
    expect(pending).not.toContain(done.id);
  });

  it("reports when nothing matches", () => {
    expect(handleQueryTaskInstances(deps, { status: "failed" })).toBe("No task instances found.");
  });
});
