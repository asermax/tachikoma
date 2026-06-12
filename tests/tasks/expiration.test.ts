import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { expireWaitingInstances } from "../../src/extensions/tasks/expiration.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
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

const waitingInstance = (updatedAt: Date) => {
  const instance = repository.createInstance({
    definitionId: null,
    taskType: "background",
    prompt: "ask the user something",
    scheduledFor: current,
  });

  repository.updateInstance(instance.id, { status: "waiting", updatedAt });
  return instance;
};

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("expireWaitingInstances", () => {
  it("fails waiting instances past the timeout and reports them", () => {
    const stale = waitingInstance(new Date("2026-06-12T07:00:00Z"));
    const onExpired = vi.fn();

    expireWaitingInstances({ repository, waitTimeoutSeconds: 7200, now, log: fakeLog, onExpired });

    const failed = repository.getInstance(stale.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("Task timed out waiting for user input after 7200s");
    expect(failed?.completedAt).toEqual(current);

    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(onExpired).toHaveBeenCalledWith(
      expect.objectContaining({ id: stale.id }),
      "Task timed out waiting for user input after 7200s",
    );
  });

  it("leaves waiting instances within the timeout untouched", () => {
    const fresh = waitingInstance(new Date("2026-06-12T09:30:00Z"));
    const onExpired = vi.fn();

    expireWaitingInstances({ repository, waitTimeoutSeconds: 7200, now, log: fakeLog, onExpired });

    expect(repository.getInstance(fresh.id)?.status).toBe("waiting");
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("ignores non-waiting instances regardless of age", () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "old but pending",
      scheduledFor: current,
    });
    repository.updateInstance(instance.id, { updatedAt: new Date("2026-06-12T01:00:00Z") });

    expireWaitingInstances({ repository, waitTimeoutSeconds: 7200, now, log: fakeLog });

    expect(repository.getInstance(instance.id)?.status).toBe("pending");
  });
});
