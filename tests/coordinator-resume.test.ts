import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import { SessionRegistry } from "../src/sessions/registry.ts";

const createFakeLog = () => {
  const log = {
    warn: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    error: vi.fn(),
  };

  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

const createFakeAgent = () =>
  ({
    open: vi.fn().mockResolvedValue({
      sessionFile: "/tmp/active.jsonl",
      dispose: vi.fn(),
      messages: [],
    }),
  }) as unknown as AgentManager;

let db: AppDatabase;
let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-coordinator-resume-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator.resumeSession guard", () => {
  it("keeps the active session when the target's pi file is missing", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);
    const agent = createFakeAgent();

    const coordinator = new Coordinator(
      registry,
      agent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const live = registry.create("test", "/tmp/active.jsonl");
    await coordinator.resumeSession(live);
    const activeId = coordinator.current()?.id;

    const target = registry.create("test", join(dir, "does-not-exist.jsonl"));
    registry.close(target.id);

    await expect(coordinator.resumeSession(target)).resolves.toBeUndefined();

    expect(coordinator.current()?.id).toBe(activeId);
    expect(log.warn).toHaveBeenCalled();
  });

  it("keeps the active session when the target has no pi file at all", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);
    const agent = createFakeAgent();

    const coordinator = new Coordinator(
      registry,
      agent,
      createRegistrations(),
      new EventBus(log),
      log,
    );

    const live = registry.create("test", "/tmp/active.jsonl");
    await coordinator.resumeSession(live);
    const activeId = coordinator.current()?.id;

    const target = registry.create("test", null);
    registry.close(target.id);

    await coordinator.resumeSession(target);

    expect(coordinator.current()?.id).toBe(activeId);
  });
});

describe("Coordinator.resumeSession bridging context", () => {
  it("injects summaries of sessions closed since the target's prior close, oldest-first", async () => {
    const log = createFakeLog();
    const registry = new SessionRegistry(db);
    const agent = createFakeAgent();

    const events = new EventBus(log);
    const coordinator = new Coordinator(registry, agent, createRegistrations(), events, log);

    const targetFile = join(dir, "target.jsonl");
    await writeFile(targetFile, "");

    const target = registry.create("test", targetFile);
    registry.update(target.id, {
      summary: "the target topic",
      closedAt: new Date("2026-01-01T00:00:00.000Z"),
    });

    const between1 = registry.create("test", join(dir, "b1.jsonl"));
    registry.update(between1.id, {
      summary: "first thing that happened",
      closedAt: new Date("2026-01-01T01:00:00.000Z"),
    });

    const between2 = registry.create("test", join(dir, "b2.jsonl"));
    registry.update(between2.id, {
      summary: "second thing that happened",
      closedAt: new Date("2026-01-01T02:00:00.000Z"),
    });

    const injected: string[] = [];

    const reloaded = registry.get(target.id);
    expect(reloaded).not.toBeNull();

    await coordinator.resumeSession(reloaded as NonNullable<typeof reloaded>);

    const factory = coordinator.hostFactory();
    let captured: { message: { content: string } } | undefined;
    const pi = {
      on: (_event: string, handler: () => { message: { content: string } } | undefined) => {
        captured = handler();
      },
    };
    factory(pi as never);

    if (captured != null) injected.push(captured.message.content);

    expect(injected).toHaveLength(1);
    expect(injected[0]).toContain('owner="bridging-context"');
    expect(injected[0]?.indexOf("first thing that happened")).toBeLessThan(
      injected[0]?.indexOf("second thing that happened") ?? -1,
    );
  });
});
