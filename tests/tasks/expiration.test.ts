import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Delivery } from "../../src/channels/types.ts";
import type { AppDatabase } from "../../src/db/index.ts";
import {
  type BackgroundSide,
  type ExecutorDeps,
  executeBackgroundInstance,
} from "../../src/extensions/tasks/executor.ts";
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

  it("expires a waiting instance produced by an unanswered ask_user pause", async () => {
    let tools: { name: string; execute: (id: string, params: unknown) => unknown }[] = [];
    const session = {
      sessionFile: "/sessions/expire.jsonl",
      messages: [] as { role: string; content: { type: string; text: string }[] }[],
      prompt: vi.fn(),
      dispose: vi.fn(),
    };
    session.prompt.mockImplementation(async () => {
      const askUser = tools.find((tool) => tool.name === "ask_user");
      await askUser?.execute("c1", { question: "Confirm?" });
      session.messages.push({
        role: "assistant",
        content: [{ type: "text", text: "waiting on confirmation" }],
      });
    });
    const openBackgroundSession = vi.fn(async (options: { customTools?: typeof tools }) => {
      tools = options.customTools ?? [];
      return session;
    });
    const side = { openBackgroundSession, classify: vi.fn() } as unknown as BackgroundSide;
    const deps: ExecutorDeps = {
      repository,
      side,
      deliver: vi.fn<(delivery: Delivery) => void>(),
      notify: vi.fn(),
      collectContext: vi.fn<ExecutorDeps["collectContext"]>().mockResolvedValue([]),
      runPostProcessors: vi.fn<ExecutorDeps["runPostProcessors"]>().mockResolvedValue(undefined),
      maxIterations: 10,
      maxConcurrent: 3,
      timezone: "UTC",
      now,
      log: fakeLog,
    };

    const instance = repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "ask before acting",
      scheduledFor: current,
    });

    await executeBackgroundInstance(deps, instance);
    expect(repository.getInstance(instance.id)?.status).toBe("waiting");

    // The user never replies; time advances past the timeout.
    current = new Date("2026-06-12T13:00:00Z");
    const onExpired = vi.fn();
    expireWaitingInstances({ repository, waitTimeoutSeconds: 7200, now, log: fakeLog, onExpired });

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toContain("timed out waiting for user input");
    expect(onExpired).toHaveBeenCalledWith(
      expect.objectContaining({ id: instance.id }),
      expect.any(String),
    );
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
