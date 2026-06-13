import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import { deliverSessionTasks } from "../../src/extensions/tasks/session-delivery.ts";
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

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("deliverSessionTasks", () => {
  it("delivers pending session instances idle-gated and completes them", () => {
    const definition = repository.createDefinition({
      name: "morning briefing",
      schedule: { type: "cron", expression: "0 9 * * *" },
      taskType: "session",
      prompt: "give me the briefing",
    });
    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: "session",
      prompt: "give me the briefing",
      scheduledFor: current,
    });
    const deliver = vi.fn();

    deliverSessionTasks({ repository, deliver, maxHoldSeconds: 900, now, log: fakeLog });

    expect(deliver).toHaveBeenCalledWith({
      text: "📋 Scheduled task: morning briefing\n\ngive me the briefing",
      gate: "idle",
      target: "agent",
      maxHoldSeconds: 900,
      metadata: { kind: "session_task", instanceId: instance.id },
    });

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("Delivered successfully");
    expect(completed?.startedAt).toEqual(current);
    expect(completed?.completedAt).toEqual(current);
  });

  it("rolls back to pending when the delivery handoff throws", () => {
    const instance = repository.createInstance({
      definitionId: null,
      taskType: "session",
      prompt: "ad-hoc task",
      scheduledFor: current,
    });
    const deliver = vi.fn().mockImplementation(() => {
      throw new Error("channel down");
    });

    deliverSessionTasks({ repository, deliver, maxHoldSeconds: 900, now, log: fakeLog });

    const rolledBack = repository.getInstance(instance.id);
    expect(rolledBack?.status).toBe("pending");
    expect(rolledBack?.startedAt).toBeNull();
  });

  it("ignores background instances", () => {
    repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "background work",
      scheduledFor: current,
    });
    const deliver = vi.fn();

    deliverSessionTasks({ repository, deliver, maxHoldSeconds: 900, now, log: fakeLog });

    expect(deliver).not.toHaveBeenCalled();
  });
});
