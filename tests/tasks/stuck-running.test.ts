import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import { failStuckRunningInstances } from "../../src/extensions/tasks/stuck-running.ts";
import type { Logger } from "../../src/log.ts";
import { createTasksTestDb } from "./setup.ts";

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;

const now = () => current;

const runningInstance = (startedAt: Date) => {
  const instance = repository.createInstance({
    definitionId: null,
    taskType: "background",
    prompt: "long running work",
    scheduledFor: current,
  });

  repository.updateInstance(instance.id, { status: "running", startedAt });
  return instance;
};

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("failStuckRunningInstances", () => {
  it("fails running instances past the timeout and reports them", () => {
    const stuck = runningInstance(new Date("2026-06-12T09:00:00Z"));
    const onStuck = vi.fn();

    failStuckRunningInstances({
      repository,
      runningTimeoutSeconds: 1800,
      now,
      log: fakeLog,
      onStuck,
    });

    const failed = repository.getInstance(stuck.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("Task exceeded running timeout of 1800s");
    expect(failed?.completedAt).toEqual(current);

    expect(onStuck).toHaveBeenCalledTimes(1);
    expect(onStuck).toHaveBeenCalledWith(
      expect.objectContaining({ id: stuck.id }),
      "Task exceeded running timeout of 1800s",
    );
  });

  it("leaves a fresh running instance within the timeout untouched", () => {
    const fresh = runningInstance(new Date("2026-06-12T09:50:00Z"));
    const onStuck = vi.fn();

    failStuckRunningInstances({
      repository,
      runningTimeoutSeconds: 1800,
      now,
      log: fakeLog,
      onStuck,
    });

    expect(repository.getInstance(fresh.id)?.status).toBe("running");
    expect(onStuck).not.toHaveBeenCalled();
  });

  it("falls back to updatedAt when startedAt was never stamped", () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "no start stamp",
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, {
      status: "running",
      updatedAt: new Date("2026-06-12T08:00:00Z"),
    });

    failStuckRunningInstances({ repository, runningTimeoutSeconds: 1800, now, log: fakeLog });

    expect(repository.getInstance(instance.id)?.status).toBe("failed");
  });

  it("ignores non-running instances regardless of age", () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "old but pending",
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, { updatedAt: new Date("2026-06-12T01:00:00Z") });

    failStuckRunningInstances({ repository, runningTimeoutSeconds: 1800, now, log: fakeLog });

    expect(repository.getInstance(instance.id)?.status).toBe("pending");
  });
});
