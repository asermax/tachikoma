import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { cleanupExpiredOneShots } from "../../src/extensions/tasks/one-shot-cleanup.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import type { TaskStatus } from "../../src/extensions/tasks/schema.ts";
import type { Logger } from "../../src/log.ts";
import { createTasksTestDb } from "./setup.ts";

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

const RETENTION_SECONDS = 172800;

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;

const now = () => current;

const firedOneShot = (firedAt: Date) => {
  const definition = repository.createDefinition({
    name: `oneshot-${firedAt.getTime()}`,
    schedule: { type: "once", at: firedAt.toISOString() },
    taskType: "background",
    prompt: "do the thing once",
  });

  repository.updateDefinition(definition.id, { lastFiredAt: firedAt, enabled: false });
  return definition;
};

const terminalInstance = (definitionId: string, status: TaskStatus, completedAt: Date) => {
  const instance = repository.createInstance({
    definitionId,
    taskType: "background",
    prompt: "do the thing once",
    scheduledFor: completedAt,
  });

  repository.updateInstance(instance.id, { status, completedAt });
  return instance;
};

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("cleanupExpiredOneShots", () => {
  it("prunes an old auto-disabled one-shot and its terminal instances", () => {
    const definition = firedOneShot(new Date("2026-06-08T10:00:00Z"));
    const instance = terminalInstance(definition.id, "completed", new Date("2026-06-08T10:05:00Z"));

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).toBeNull();
    expect(repository.getInstance(instance.id)).toBeNull();
  });

  it("keeps a recently fired one-shot within the retention window", () => {
    const definition = firedOneShot(new Date("2026-06-12T09:00:00Z"));
    terminalInstance(definition.id, "completed", new Date("2026-06-12T09:05:00Z"));

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).not.toBeNull();
  });

  it("keeps a one-shot with a non-terminal instance even if old", () => {
    const definition = firedOneShot(new Date("2026-06-08T10:00:00Z"));
    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: "do the thing once",
      scheduledFor: new Date("2026-06-08T10:00:00Z"),
    });
    repository.updateInstance(instance.id, { status: "running" });

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).not.toBeNull();
  });

  it("anchors retention on the latest completion, not lastFiredAt", () => {
    const definition = firedOneShot(new Date("2026-06-08T10:00:00Z"));
    terminalInstance(definition.id, "completed", new Date("2026-06-12T09:00:00Z"));

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).not.toBeNull();
  });

  it("prunes a fired one-shot that never produced an instance", () => {
    const definition = firedOneShot(new Date("2026-06-08T10:00:00Z"));

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).toBeNull();
  });

  it("leaves enabled (still-pending) one-shot definitions alone", () => {
    const definition = repository.createDefinition({
      name: "future-oneshot",
      schedule: { type: "once", at: "2026-06-20T10:00:00Z" },
      taskType: "background",
      prompt: "future work",
    });

    cleanupExpiredOneShots({ repository, retentionSeconds: RETENTION_SECONDS, log: fakeLog });

    expect(repository.getDefinition(definition.id)).not.toBeNull();
  });
});
