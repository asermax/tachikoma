import { beforeEach, describe, expect, it } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import { createTasksTestDb } from "./setup.ts";

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;

const now = () => current;

const makeDefinition = (
  overrides: Partial<Parameters<TaskRepository["createDefinition"]>[0]> = {},
) =>
  repository.createDefinition({
    name: `def-${Math.random().toString(36).slice(2)}`,
    schedule: { type: "cron", expression: "* * * * *" },
    taskType: "background",
    prompt: "do the work",
    ...overrides,
  });

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("TaskRepository definitions", () => {
  it("defaults the clock to the real Date when no `now` is supplied", () => {
    // The timestamp column stores whole seconds, so allow a one-second floor.
    const before = Math.floor(Date.now() / 1000) - 1;
    const repo = new TaskRepository(db);

    const definition = repo.createDefinition({
      name: "real-clock",
      schedule: { type: "cron", expression: "* * * * *" },
      taskType: "background",
      prompt: "p",
    });

    expect(Math.floor(definition.createdAt.getTime() / 1000)).toBeGreaterThanOrEqual(before);
  });

  it("honors an explicit enabled flag instead of defaulting to true", () => {
    const definition = makeDefinition({ enabled: false });

    expect(definition.enabled).toBe(false);
  });

  it("returns null for an unknown definition id and name", () => {
    expect(repository.getDefinition("missing")).toBeNull();
    expect(repository.getDefinitionByName("missing")).toBeNull();
  });

  it("resolves a definition by id, then by name", () => {
    const definition = makeDefinition({ name: "by-name" });

    expect(repository.resolveDefinition(definition.id)?.id).toBe(definition.id);
    expect(repository.resolveDefinition("by-name")?.id).toBe(definition.id);
    expect(repository.resolveDefinition("nope")).toBeNull();
  });

  it("deletes a definition and reports whether a row was removed", () => {
    const definition = makeDefinition();

    expect(repository.deleteDefinition(definition.id)).toBe(true);
    expect(repository.deleteDefinition(definition.id)).toBe(false);
  });

  it("lists enabled and disabled definitions separately", () => {
    const enabled = makeDefinition({ enabled: true });
    const disabled = makeDefinition({ enabled: false });

    expect(repository.listEnabledDefinitions().map((d) => d.id)).toEqual([enabled.id]);
    expect(repository.listDisabledDefinitions().map((d) => d.id)).toEqual([disabled.id]);
  });

  it("stamps `since` on update and returns null for a missing id", () => {
    const definition = makeDefinition();
    current = new Date("2026-06-12T12:00:00Z");

    const updated = repository.updateDefinition(definition.id, { name: "renamed" });
    expect(updated?.name).toBe("renamed");
    expect(updated?.since).toEqual(current);

    expect(repository.updateDefinition("missing", { name: "x" })).toBeNull();
  });

  it("persists a goal provided at creation and reads null when omitted", () => {
    const withGoal = makeDefinition({ goal: "ship the release with all tests green" });
    expect(withGoal.goal).toBe("ship the release with all tests green");

    const withoutGoal = makeDefinition();
    expect(withoutGoal.goal).toBeNull();
  });

  it("updates the goal via updateDefinition", () => {
    const definition = makeDefinition();
    expect(definition.goal).toBeNull();

    const updated = repository.updateDefinition(definition.id, { goal: "a new goal" });
    expect(updated?.goal).toBe("a new goal");
    expect(repository.getDefinition(definition.id)?.goal).toBe("a new goal");
  });
});

describe("TaskRepository instances", () => {
  const instance = (overrides: Partial<Parameters<TaskRepository["createInstance"]>[0]> = {}) =>
    repository.createInstance({
      definitionId: null,
      taskType: "background",
      prompt: "task prompt",
      scheduledFor: current,
      ...overrides,
    });

  it("returns null for a missing instance", () => {
    expect(repository.getInstance("missing")).toBeNull();
  });

  it("returns the latest instance for a definition by creation order", () => {
    const definition = makeDefinition();

    const first = instance({ definitionId: definition.id });
    current = new Date("2026-06-12T11:00:00Z");
    const second = instance({ definitionId: definition.id });

    expect(repository.getLatestInstanceForDefinition(definition.id)?.id).toBe(second.id);
    expect(first.id).not.toBe(second.id);
    expect(repository.getLatestInstanceForDefinition("missing")).toBeNull();
  });

  it("finds an active instance without a scheduledFor (status-only check)", () => {
    const definition = makeDefinition();
    const created = instance({ definitionId: definition.id });
    repository.updateInstance(created.id, { status: "running" });

    expect(repository.getActiveInstanceForDefinition(definition.id)?.id).toBe(created.id);

    repository.updateInstance(created.id, { status: "completed" });
    expect(repository.getActiveInstanceForDefinition(definition.id)).toBeNull();
  });

  it("finds a period-covering instance when a scheduledFor is given", () => {
    const definition = makeDefinition();
    const scheduledFor = new Date("2026-06-12T10:35:00Z");
    const created = instance({ definitionId: definition.id, scheduledFor });
    repository.updateInstance(created.id, { status: "completed" });

    expect(repository.getActiveInstanceForDefinition(definition.id, scheduledFor)?.id).toBe(
      created.id,
    );

    expect(
      repository.getActiveInstanceForDefinition(definition.id, new Date("2026-06-12T10:40:00Z")),
    ).toBeNull();
  });

  it("updateInstance returns null for a missing id", () => {
    expect(repository.updateInstance("missing", { status: "running" })).toBeNull();
  });

  it("queryInstances filters by status, taskType, and definitionId and honors the limit", () => {
    const definition = makeDefinition();
    const matching = instance({ definitionId: definition.id });
    repository.updateInstance(matching.id, { status: "running" });

    const other = instance();
    repository.updateInstance(other.id, { status: "pending" });

    const byStatus = repository.queryInstances({ status: "running" });
    expect(byStatus.map((i) => i.id)).toEqual([matching.id]);

    const byDefinition = repository.queryInstances({ definitionId: definition.id });
    expect(byDefinition.map((i) => i.id)).toEqual([matching.id]);

    const byType = repository.queryInstances({ taskType: "background" });
    expect(byType.length).toBeGreaterThanOrEqual(2);

    expect(repository.queryInstances({ limit: 1 })).toHaveLength(1);
  });

  it("queryInstances returns all rows when no filters are passed", () => {
    instance();
    instance();

    expect(repository.queryInstances({})).toHaveLength(2);
  });

  it("getPendingInstances and getResumableInstances filter on status and userResponse", () => {
    const pending = instance();

    const waitingNoReply = instance();
    repository.updateInstance(waitingNoReply.id, { status: "waiting" });

    const resumable = instance();
    repository.updateInstance(resumable.id, { status: "waiting", userResponse: "reply" });

    expect(repository.getPendingInstances("background").map((i) => i.id)).toEqual([pending.id]);
    expect(repository.getResumableInstances("background").map((i) => i.id)).toEqual([resumable.id]);
  });

  it("listExpiredWaitingInstances returns only stale waiting rows", () => {
    const stale = instance();
    repository.updateInstance(stale.id, {
      status: "waiting",
      updatedAt: new Date("2026-06-12T07:00:00Z"),
    });

    const fresh = instance();
    repository.updateInstance(fresh.id, {
      status: "waiting",
      updatedAt: new Date("2026-06-12T09:59:00Z"),
    });

    const expired = repository.listExpiredWaitingInstances(3600);
    expect(expired.map((i) => i.id)).toEqual([stale.id]);
  });

  it("markRunningAsFailed flips every running instance and returns the count", () => {
    const running = instance();
    repository.updateInstance(running.id, { status: "running" });
    const pending = instance();

    expect(repository.markRunningAsFailed("process restart")).toBe(1);

    const failed = repository.getInstance(running.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("Task failed: process restart");
    expect(failed?.completedAt).toEqual(current);

    expect(repository.getInstance(pending.id)?.status).toBe("pending");
  });

  it("persists a goal snapshotted onto an instance and reads null when omitted", () => {
    const withGoal = instance({ goal: "instance goal" });
    expect(withGoal.goal).toBe("instance goal");

    const withoutGoal = instance();
    expect(withoutGoal.goal).toBeNull();
  });
});
