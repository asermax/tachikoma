import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import {
  createTaskInteractiveToolsFactory,
  createTaskToolsFactory,
  handleCreateTask,
  handleDeleteTask,
  handleGetTask,
  handleListTasks,
  handleQueryTaskInstances,
  handleRespondToTask,
  handleRunTaskNow,
  handleStopTask,
  handleUpdateTask,
  type ToolDeps,
} from "../../src/extensions/tasks/tools.ts";
import type { Logger } from "../../src/log.ts";
import { createTasksTestDb } from "./setup.ts";

const registeredToolNames = (factory: ExtensionFactory): string[] => {
  const names: string[] = [];
  const pi = { registerTool: (tool: { name: string }) => names.push(tool.name) };

  factory(pi as unknown as Parameters<ExtensionFactory>[0]);

  return names;
};

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;
let deps: ToolDeps;

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, () => current);
  deps = { repository, timezone: "UTC", now: () => current, log: fakeLog };
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

  it("persists an optional goal on the definition", () => {
    handleCreateTask(deps, {
      name: "goaled",
      schedule: "0 9 * * *",
      type: "background",
      prompt: "do the work",
      goal: "the work is done and verified",
    });

    expect(repository.listEnabledDefinitions()[0]?.goal).toBe("the work is done and verified");
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

  it("updates the goal when provided", () => {
    const definition = repository.createDefinition({
      name: "task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });

    handleUpdateTask(deps, { task_id: definition.id, goal: "a new goal" });

    expect(repository.getDefinition(definition.id)?.goal).toBe("a new goal");
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

describe("handleGetTask", () => {
  it("returns full details by id, including the latest instance summary", () => {
    const definition = repository.createDefinition({
      name: "briefing",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "background",
      prompt: "do the briefing in full detail",
    });
    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: definition.prompt,
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, { status: "completed", result: "all done" });

    const message = handleGetTask(deps, { task: definition.id });

    expect(message).toContain("# briefing");
    expect(message).toContain(`- ID: ${definition.id}`);
    expect(message).toContain("- Type: background");
    expect(message).toContain("do the briefing in full detail");
    expect(message).toContain("## Latest instance");
    expect(message).toContain(instance.id);
    expect(message).toContain("Result: all done");
  });

  it("resolves a definition by exact name", () => {
    repository.createDefinition({
      name: "named task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });

    expect(handleGetTask(deps, { task: "named task" })).toContain("# named task");
  });

  it("throws for an unknown task", () => {
    expect(() => handleGetTask(deps, { task: "missing" })).toThrow("Task 'missing' not found.");
  });
});

describe("handleDeleteTask", () => {
  it("deletes a definition by id", () => {
    const definition = repository.createDefinition({
      name: "doomed",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });

    expect(handleDeleteTask(deps, { task: definition.id })).toBe("Task 'doomed' deleted.");
    expect(repository.getDefinition(definition.id)).toBeNull();
  });

  it("deletes a definition by exact name", () => {
    const definition = repository.createDefinition({
      name: "delete me",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "prompt",
    });

    expect(handleDeleteTask(deps, { task: "delete me" })).toBe("Task 'delete me' deleted.");
    expect(repository.getDefinition(definition.id)).toBeNull();
  });

  it("throws for an unknown task", () => {
    expect(() => handleDeleteTask(deps, { task: "missing" })).toThrow("Task 'missing' not found.");
  });
});

describe("handleRunTaskNow", () => {
  it("queues an instance by reference, snapshotting the prompt without mutating the definition", () => {
    const definition = repository.createDefinition({
      name: "reference task",
      schedule: { type: "once", at: "2026-08-01T09:00:00.000Z" },
      taskType: "background",
      prompt: "reference prompt",
      enabled: false,
    });

    const message = handleRunTaskNow(deps, { task: definition.id });

    expect(message).toContain("background task 'reference task' queued.");

    const instances = repository.queryInstances({ definitionId: definition.id });
    expect(instances).toHaveLength(1);
    expect(instances[0]).toMatchObject({
      taskType: "background",
      prompt: "reference prompt",
      status: "pending",
      scheduledFor: current,
    });

    // The auto-disabled one-shot definition is left untouched.
    expect(repository.getDefinition(definition.id)).toMatchObject({
      enabled: false,
      lastFiredAt: null,
    });
  });

  it("queues an ad-hoc instance with an inline prompt and explicit type", () => {
    const message = handleRunTaskNow(deps, {
      prompt: "ad-hoc work",
      type: "session",
      name: "quick job",
    });

    expect(message).toContain("session task 'quick job' queued.");

    const instances = repository.queryInstances({ taskType: "session" });
    expect(instances).toHaveLength(1);
    expect(instances[0]).toMatchObject({
      definitionId: null,
      prompt: "ad-hoc work",
      status: "pending",
    });
  });

  it("defaults an ad-hoc run to background", () => {
    handleRunTaskNow(deps, { prompt: "background by default" });

    expect(repository.queryInstances({ taskType: "background" })).toHaveLength(1);
  });

  it("rejects providing both task and prompt", () => {
    expect(() => handleRunTaskNow(deps, { task: "x", prompt: "y" })).toThrow(
      "Provide exactly one of 'task' or 'prompt', not both.",
    );
  });

  it("rejects providing neither task nor prompt", () => {
    expect(() => handleRunTaskNow(deps, {})).toThrow("Either 'task' or 'prompt' is required.");
  });

  it("rejects 'name' in by-reference mode", () => {
    const definition = repository.createDefinition({
      name: "task",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "background",
      prompt: "prompt",
    });

    expect(() => handleRunTaskNow(deps, { task: definition.id, name: "label" })).toThrow(
      "'name' can only be used with 'prompt'.",
    );
  });

  it("throws for an unknown referenced task", () => {
    expect(() => handleRunTaskNow(deps, { task: "missing" })).toThrow("Task 'missing' not found.");
  });

  it("snapshots the definition goal on a by-reference run", () => {
    const definition = repository.createDefinition({
      name: "reference task",
      schedule: { type: "once", at: "2026-08-01T09:00:00.000Z" },
      taskType: "background",
      prompt: "reference prompt",
      goal: "definition goal",
    });

    handleRunTaskNow(deps, { task: definition.id });

    expect(repository.queryInstances({ definitionId: definition.id })[0]?.goal).toBe(
      "definition goal",
    );
  });

  it("overrides the definition goal with an explicit run-now goal", () => {
    const definition = repository.createDefinition({
      name: "reference task",
      schedule: { type: "once", at: "2026-08-01T09:00:00.000Z" },
      taskType: "background",
      prompt: "reference prompt",
      goal: "definition goal",
    });

    handleRunTaskNow(deps, { task: definition.id, goal: "override goal" });

    expect(repository.queryInstances({ definitionId: definition.id })[0]?.goal).toBe(
      "override goal",
    );
  });

  it("carries an inline goal on an ad-hoc run", () => {
    handleRunTaskNow(deps, { prompt: "ad-hoc work", goal: "ad-hoc goal" });

    expect(repository.queryInstances({})[0]?.goal).toBe("ad-hoc goal");
  });

  it("creates an ad-hoc instance with goal null when no goal is given", () => {
    handleRunTaskNow(deps, { prompt: "ad-hoc work", type: "background" });

    expect(repository.queryInstances({ taskType: "background" })[0]?.goal).toBeNull();
  });
});

describe("handleRespondToTask", () => {
  const waitingInstance = () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "ask the user",
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, { status: "waiting", question: "left or right?" });
    return instance;
  };

  it("stores the response and leaves the instance waiting for the runner to resume", () => {
    const instance = waitingInstance();

    const message = handleRespondToTask(deps, {
      task_instance_id: instance.id,
      response: "  left  ",
    });

    expect(message).toContain("Response sent.");

    const updated = repository.getInstance(instance.id);
    expect(updated?.status).toBe("waiting");
    expect(updated?.userResponse).toBe("left");
  });

  it("rejects an empty response", () => {
    const instance = waitingInstance();
    expect(() =>
      handleRespondToTask(deps, { task_instance_id: instance.id, response: "   " }),
    ).toThrow("Response cannot be empty.");
  });

  it("rejects responding to an unknown instance", () => {
    expect(() => handleRespondToTask(deps, { task_instance_id: "nope", response: "x" })).toThrow(
      "not found",
    );
  });

  it("rejects responding to an instance that is not waiting", () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "running task",
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, { status: "running" });

    expect(() =>
      handleRespondToTask(deps, { task_instance_id: instance.id, response: "x" }),
    ).toThrow("not waiting for input");
  });

  it("rejects a second response while one is already pending", () => {
    const instance = waitingInstance();
    repository.updateInstance(instance.id, { userResponse: "already here" });

    expect(() =>
      handleRespondToTask(deps, { task_instance_id: instance.id, response: "x" }),
    ).toThrow("already pending");
  });
});

describe("handleStopTask", () => {
  const makeInstance = (status: "pending" | "running" | "waiting" | "completed" | "failed") => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "do some work",
      scheduledFor: current,
    });

    if (status !== "pending") {
      repository.updateInstance(instance.id, {
        status,
        question: status === "waiting" ? "?" : null,
      });
    }

    return instance;
  };

  const cancelDeps = (cancel: ReturnType<typeof vi.fn>) => ({
    ...deps,
    cancelRunningInstance: cancel,
  });

  it("cancels a pending instance so it never runs", async () => {
    const cancel = vi.fn().mockResolvedValue(false);
    const instance = makeInstance("pending");

    const message = await handleStopTask(cancelDeps(cancel), { task_instance_id: instance.id });

    expect(message).toContain(instance.id);
    expect(message).toContain("pending");

    const updated = repository.getInstance(instance.id);
    expect(updated?.status).toBe("failed");
    expect(updated?.result).toContain("cancelled");
    expect(updated?.completedAt).toEqual(current);
  });

  it("cancels a waiting instance and dismisses its pending question", async () => {
    const cancel = vi.fn().mockResolvedValue(false);
    const instance = makeInstance("waiting");

    await handleStopTask(cancelDeps(cancel), { task_instance_id: instance.id });

    expect(repository.getInstance(instance.id)?.status).toBe("failed");
    expect(
      repository.getResumableInstances("background").find((row) => row.id === instance.id),
    ).toBeUndefined();
  });

  it("marks a running instance failed and aborts the live run", async () => {
    const cancel = vi.fn().mockResolvedValue(true);
    const instance = makeInstance("running");

    const message = await handleStopTask(cancelDeps(cancel), { task_instance_id: instance.id });

    expect(repository.getInstance(instance.id)?.status).toBe("failed");
    expect(cancel).toHaveBeenCalledWith(instance.id);
    expect(message).toContain("running");
  });

  it("rejects an unknown instance without touching anything", async () => {
    const cancel = vi.fn().mockResolvedValue(false);

    await expect(handleStopTask(cancelDeps(cancel), { task_instance_id: "nope" })).rejects.toThrow(
      "not found",
    );
    expect(cancel).not.toHaveBeenCalled();
  });

  it("rejects an already-completed instance", async () => {
    const cancel = vi.fn().mockResolvedValue(false);
    const instance = makeInstance("completed");

    await expect(
      handleStopTask(cancelDeps(cancel), { task_instance_id: instance.id }),
    ).rejects.toThrow("already finished");
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
    expect(cancel).not.toHaveBeenCalled();
  });

  it("rejects an already-failed instance and leaves its result intact", async () => {
    const cancel = vi.fn().mockResolvedValue(false);
    const instance = makeInstance("failed");
    repository.updateInstance(instance.id, { result: "earlier error" });

    await expect(
      handleStopTask(cancelDeps(cancel), { task_instance_id: instance.id }),
    ).rejects.toThrow("already finished");
    expect(repository.getInstance(instance.id)?.result).toBe("earlier error");
    expect(cancel).not.toHaveBeenCalled();
  });
});

describe("tool factory split", () => {
  it("keeps the interactive respond_to_task out of the background-bound toolset", () => {
    const operational = registeredToolNames(createTaskToolsFactory(deps));

    expect(operational).toEqual([
      "create_task",
      "update_task",
      "list_tasks",
      "get_task",
      "delete_task",
      "run_task_now",
      "query_task_instances",
      "stop_task",
    ]);
    expect(operational).not.toContain("respond_to_task");
  });

  it("registers respond_to_task only in the interactive (foreground) factory", () => {
    expect(registeredToolNames(createTaskInteractiveToolsFactory(deps))).toEqual([
      "respond_to_task",
    ]);
  });
});
