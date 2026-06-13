import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import { SessionRegistry } from "../src/sessions/registry.ts";

let db: AppDatabase;

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-db-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

describe("KeyValueState", () => {
  it("round-trips values per namespace", () => {
    const memory = new KeyValueState(db, "memory");
    const tasks = new KeyValueState(db, "tasks");

    memory.set("lastTick", { at: 123 });
    tasks.set("lastTick", { at: 456 });

    expect(memory.get("lastTick")).toEqual({ at: 123 });
    expect(tasks.get("lastTick")).toEqual({ at: 456 });

    memory.delete("lastTick");
    expect(memory.get("lastTick")).toBeNull();
  });
});

describe("SessionRegistry", () => {
  it("creates, updates, closes and reopens sessions", () => {
    const registry = new SessionRegistry(db);

    const session = registry.create("repl", "/tmp/session.jsonl");
    expect(session.id).toBeGreaterThan(0);
    expect(session.closedAt).toBeNull();

    registry.update(session.id, { summary: "talked about cats" });
    const closed = registry.close(session.id);
    expect(closed.closedAt).not.toBeNull();

    const reopened = registry.reopen(session.id);
    expect(reopened.closedAt).toBeNull();
    expect(reopened.lastResumedAt).not.toBeNull();
    expect(reopened.summary).toBe("talked about cats");
  });

  it("lists recently closed sessions as resumable", () => {
    const registry = new SessionRegistry(db);

    const recent = registry.create("repl", null);
    registry.close(recent.id);

    const resumable = registry.listResumable(3600);
    expect(resumable.map((s) => s.id)).toContain(recent.id);
    expect(registry.listResumable(0)).toHaveLength(0);
  });

  it("finds sessions left open by a previous run", () => {
    const registry = new SessionRegistry(db);

    const dangling = registry.create("repl", null);
    const closed = registry.create("repl", null);
    registry.close(closed.id);

    expect(registry.findDangling().map((s) => s.id)).toEqual([dangling.id]);
  });

  it("maps channel messages back to their owning session", () => {
    const registry = new SessionRegistry(db);
    const session = registry.create("telegram", null);

    registry.recordChannelMessage("telegram", "42", session.id, "outgoing");

    expect(registry.findSessionByMessageId("telegram", "42")?.id).toBe(session.id);
    expect(registry.findSessionByMessageId("telegram", "999")).toBeNull();
  });

  it("re-points a channel message id on conflict", () => {
    const registry = new SessionRegistry(db);
    const first = registry.create("telegram", null);
    const second = registry.create("telegram", null);

    registry.recordChannelMessage("telegram", "42", first.id, "outgoing");
    registry.recordChannelMessage("telegram", "42", second.id, "incoming");

    expect(registry.findSessionByMessageId("telegram", "42")?.id).toBe(second.id);
  });
});
