import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { generateDueInstances } from "../../src/extensions/tasks/generation.ts";
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

const tick = () => generateDueInstances({ repository, timezone: "UTC", now, log: fakeLog });

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:30:00Z");
  repository = new TaskRepository(db, now);
});

describe("cron instance generation", () => {
  it("creates a pending instance when a cron occurrence is due", () => {
    const definition = repository.createDefinition({
      name: "five-minutely",
      schedule: { type: "cron", expression: "*/5 * * * *" },
      taskType: "session",
      prompt: "check things",
    });

    current = new Date("2026-06-12T10:36:00Z");
    tick();

    const instances = repository.queryInstances({});
    expect(instances).toHaveLength(1);
    expect(instances[0]).toMatchObject({
      definitionId: definition.id,
      taskType: "session",
      status: "pending",
      prompt: "check things",
    });
    expect(instances[0]?.scheduledFor).toEqual(new Date("2026-06-12T10:35:00Z"));

    expect(repository.getDefinition(definition.id)?.lastFiredAt).toEqual(current);
  });

  it("suppresses duplicates for an already covered period", () => {
    const definition = repository.createDefinition({
      name: "five-minutely",
      schedule: { type: "cron", expression: "*/5 * * * *" },
      taskType: "session",
      prompt: "check things",
    });

    current = new Date("2026-06-12T10:36:00Z");
    tick();
    expect(repository.queryInstances({})).toHaveLength(1);

    // Even if the anchor is rewound, the existing instance covers the 10:35
    // period and must suppress regeneration.
    repository.updateDefinition(definition.id, {
      lastFiredAt: null,
      since: new Date("2026-06-12T10:34:00Z"),
    });
    tick();

    expect(repository.queryInstances({})).toHaveLength(1);
  });

  it("does not fire for occurrences that predate the definition (stale-cron prevention)", () => {
    // Created at 10:30 with a daily-at-10:00 cron: today's occurrence already passed.
    repository.createDefinition({
      name: "daily",
      schedule: { type: "cron", expression: "0 10 * * *" },
      taskType: "background",
      prompt: "daily check",
    });

    current = new Date("2026-06-12T10:31:00Z");
    tick();
    expect(repository.queryInstances({})).toHaveLength(0);

    current = new Date("2026-06-13T10:01:00Z");
    tick();

    const instances = repository.queryInstances({});
    expect(instances).toHaveLength(1);
    expect(instances[0]?.scheduledFor).toEqual(new Date("2026-06-13T10:00:00Z"));
  });

  it("skips definitions with an invalid cron expression without aborting the pass", () => {
    repository.createDefinition({
      name: "broken",
      schedule: { type: "cron", expression: "not a cron" },
      taskType: "session",
      prompt: "never",
    });
    repository.createDefinition({
      name: "valid",
      schedule: { type: "cron", expression: "* * * * *" },
      taskType: "session",
      prompt: "every minute",
    });

    current = new Date("2026-06-12T10:31:00Z");
    tick();

    const instances = repository.queryInstances({});
    expect(instances).toHaveLength(1);
    expect(instances[0]?.prompt).toBe("every minute");
  });
});

describe("one-shot instance generation", () => {
  it("fires a due one-shot and auto-disables its definition", () => {
    const definition = repository.createDefinition({
      name: "reminder",
      schedule: { type: "once", at: "2026-06-12T10:45:00.000Z" },
      taskType: "session",
      prompt: "remind me",
    });

    tick();
    expect(repository.queryInstances({})).toHaveLength(0);

    current = new Date("2026-06-12T10:46:00Z");
    tick();

    const instances = repository.queryInstances({});
    expect(instances).toHaveLength(1);
    expect(instances[0]?.scheduledFor).toEqual(new Date("2026-06-12T10:45:00Z"));

    const updated = repository.getDefinition(definition.id);
    expect(updated?.enabled).toBe(false);
    expect(updated?.lastFiredAt).toEqual(current);

    // Disabled after firing: further ticks create nothing.
    current = new Date("2026-06-12T10:47:00Z");
    tick();
    expect(repository.queryInstances({})).toHaveLength(1);
  });

  it("skips a one-shot that already has an active instance", () => {
    const definition = repository.createDefinition({
      name: "reminder",
      schedule: { type: "once", at: "2026-06-12T10:00:00.000Z" },
      taskType: "background",
      prompt: "remind me",
    });
    repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: "remind me",
      scheduledFor: new Date("2026-06-12T10:00:00Z"),
    });

    tick();

    expect(repository.queryInstances({})).toHaveLength(1);
  });
});
