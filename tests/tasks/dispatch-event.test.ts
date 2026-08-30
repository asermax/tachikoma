import { describe, expect, it, vi } from "vitest";

import { DISPATCH_BACKGROUND_TASK_EVENT, EventBus } from "../../src/events.ts";
import type { AppContext } from "../../src/extensions/api.ts";
import tasks from "../../src/extensions/tasks/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import type { Logger } from "../../src/log.ts";
import { createTasksTestDb } from "./setup.ts";

// The subscription is wired inside tasks' setup (DES-002: task-creation logic never leaves the
// extension), so these tests drive the real setup against a real SQLite database (DES-003) and a
// real EventBus — only the surrounding app services are fakes.
const setupTasks = async (): Promise<{
  bus: EventBus;
  busLog: { error: ReturnType<typeof vi.fn> };
  appLog: { info: ReturnType<typeof vi.fn>; warn: ReturnType<typeof vi.fn> };
  repository: TaskRepository;
}> => {
  const db = await createTasksTestDb();
  const busLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const appLog = { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const bus = new EventBus(busLog as unknown as Logger);

  tasks.setup({
    extensionConfig: {
      backgroundMaxIterations: 10,
      backgroundMaxConcurrent: 3,
      waitTimeoutSeconds: 7200,
      runningTimeoutSeconds: 1800,
      oneShotRetentionSeconds: 172800,
    },
    config: { scheduler: { timezone: "UTC" } },
    db,
    log: appLog as unknown as Logger,
    events: bus,
    scheduler: { every: vi.fn(), cron: vi.fn() },
    agent: { use: vi.fn(), side: { run: vi.fn() } },
    channels: { register: vi.fn(), deliver: vi.fn() },
    sessions: { registerProcessor: vi.fn(), runPostProcessors: vi.fn() },
    bootstrap: vi.fn(),
  } as unknown as AppContext);

  return { bus, busLog, appLog, repository: new TaskRepository(db) };
};

// EventBus dispatches handlers on a microtask — flush one macrotask so synchronous
// handler bodies (SQLite writes) have landed before assertions read them.
const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

const emit = (bus: EventBus, payload: unknown): void => {
  bus.emit(DISPATCH_BACKGROUND_TASK_EVENT, payload);
};

describe("tasks subscription to DISPATCH_BACKGROUND_TASK_EVENT", () => {
  it("a valid payload creates one pending background instance with no definition", async () => {
    const { bus, repository, appLog, busLog } = await setupTasks();

    emit(bus, {
      prompt: "Report tonight's skill proposals",
      goal: "Follow up on the proposals",
      source: "skill-evolution",
    });
    await flush();

    const pending = repository.getPendingInstances("background");

    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({
      definitionId: null,
      taskType: "background",
      status: "pending",
      prompt: "Report tonight's skill proposals",
      goal: "Follow up on the proposals",
    });
    expect(appLog.info).toHaveBeenCalledWith(
      expect.objectContaining({ source: "skill-evolution" }),
      "background task instance created from dispatch event",
    );
    // The subscriber never threw into the emitter.
    expect(busLog.error).not.toHaveBeenCalled();
  });

  it("normalizes a missing or empty goal to null (the runner can extract one later)", async () => {
    const { bus, repository } = await setupTasks();

    emit(bus, { prompt: "p1", source: "skill-evolution" });
    emit(bus, { prompt: "p2", goal: "", source: "skill-evolution" });
    await flush();

    const pending = repository.getPendingInstances("background");

    expect(pending.map((instance) => instance.goal)).toEqual([null, null]);
  });

  it.each([
    ["an empty prompt", { prompt: "", source: "skill-evolution" }],
    ["a blank prompt", { prompt: "   ", source: "skill-evolution" }],
    ["a non-string prompt", { prompt: 42, source: "skill-evolution" }],
    ["no prompt at all", { goal: "g", source: "skill-evolution" }],
    ["a null payload", null],
  ])(
    "drops %s — no instance, a warning, and nothing thrown into the emitter",
    async (_name, payload) => {
      const { bus, repository, appLog, busLog } = await setupTasks();

      emit(bus, payload);
      await flush();

      expect(repository.getPendingInstances("background")).toEqual([]);
      expect(appLog.warn).toHaveBeenCalledWith(
        expect.objectContaining({}),
        "dispatch-background-task payload has no prompt — dropped",
      );
      expect(busLog.error).not.toHaveBeenCalled();
    },
  );
});
