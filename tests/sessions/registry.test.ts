import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { SessionRegistry } from "../../src/sessions/registry.ts";

let db: AppDatabase;

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-registry-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

describe("SessionRegistry.listResumable", () => {
  it("excludes closed sessions that have no pi session file", () => {
    const registry = new SessionRegistry(db);

    const withFile = registry.create("repl", "/tmp/with-file.jsonl");
    const withoutFile = registry.create("repl", null);
    registry.close(withFile.id);
    registry.close(withoutFile.id);

    const ids = registry.listResumable(3600).map((session) => session.id);

    expect(ids).toContain(withFile.id);
    expect(ids).not.toContain(withoutFile.id);
  });
});

describe("SessionRegistry.listClosedBetween", () => {
  it("returns summarized sessions closed in (start, end] oldest-first", () => {
    const registry = new SessionRegistry(db);

    const start = new Date("2026-01-01T00:00:00.000Z");

    const before = registry.create("repl", "/tmp/a.jsonl");
    const first = registry.create("repl", "/tmp/b.jsonl");
    const second = registry.create("repl", "/tmp/c.jsonl");
    const after = registry.create("repl", "/tmp/d.jsonl");

    registry.update(before.id, {
      summary: "before window",
      closedAt: new Date("2025-12-31T23:00:00.000Z"),
    });
    registry.update(first.id, {
      summary: "first inside",
      closedAt: new Date("2026-01-01T01:00:00.000Z"),
    });
    registry.update(second.id, {
      summary: "second inside",
      closedAt: new Date("2026-01-01T02:00:00.000Z"),
    });
    registry.update(after.id, {
      summary: "after window",
      closedAt: new Date("2026-01-01T05:00:00.000Z"),
    });

    const end = new Date("2026-01-01T03:00:00.000Z");
    const summaries = registry.listClosedBetween(start, end).map((session) => session.summary);

    expect(summaries).toEqual(["first inside", "second inside"]);
  });

  it("skips sessions without a summary", () => {
    const registry = new SessionRegistry(db);

    const start = new Date("2026-01-01T00:00:00.000Z");

    const summarized = registry.create("repl", "/tmp/a.jsonl");
    const bare = registry.create("repl", "/tmp/b.jsonl");

    registry.update(summarized.id, {
      summary: "kept",
      closedAt: new Date("2026-01-01T01:00:00.000Z"),
    });
    registry.update(bare.id, { closedAt: new Date("2026-01-01T02:00:00.000Z") });

    const end = new Date("2026-01-01T03:00:00.000Z");
    const ids = registry.listClosedBetween(start, end).map((session) => session.id);

    expect(ids).toEqual([summarized.id]);
  });
});

describe("SessionRegistry.get", () => {
  it("returns a created session by id", () => {
    const registry = new SessionRegistry(db);

    const created = registry.create("repl", "/tmp/s.jsonl");

    expect(registry.get(created.id)?.id).toBe(created.id);
  });

  it("returns null for an unknown id", () => {
    const registry = new SessionRegistry(db);

    expect(registry.get(9999)).toBeNull();
  });
});

describe("SessionRegistry close/reopen", () => {
  it("close stamps closedAt and reopen clears it while stamping lastResumedAt", () => {
    const registry = new SessionRegistry(db);

    const created = registry.create("repl", "/tmp/s.jsonl");

    const closed = registry.close(created.id);
    expect(closed.closedAt).not.toBeNull();

    const reopened = registry.reopen(created.id);
    expect(reopened.closedAt).toBeNull();
    expect(reopened.lastResumedAt).not.toBeNull();
  });
});

describe("SessionRegistry.findDangling", () => {
  it("returns only sessions that were never closed", () => {
    const registry = new SessionRegistry(db);

    const open = registry.create("repl", "/tmp/open.jsonl");
    const closed = registry.create("repl", "/tmp/closed.jsonl");
    registry.close(closed.id);

    const ids = registry.findDangling().map((session) => session.id);

    expect(ids).toContain(open.id);
    expect(ids).not.toContain(closed.id);
  });
});

describe("SessionRegistry.markErrored", () => {
  it("sets the error flag, quarantining the session", () => {
    const registry = new SessionRegistry(db);

    const created = registry.create("repl", "/tmp/s.jsonl");
    expect(created.error).toBe(false);

    const errored = registry.markErrored(created.id);

    expect(errored.error).toBe(true);
    expect(registry.get(created.id)?.error).toBe(true);
  });
});

describe("SessionRegistry.listResumable errored exclusion", () => {
  it("excludes an errored session that otherwise meets the resumable predicates", () => {
    const registry = new SessionRegistry(db);

    const healthy = registry.create("repl", "/tmp/healthy.jsonl");
    const broken = registry.create("repl", "/tmp/broken.jsonl");
    registry.close(healthy.id);
    registry.close(broken.id);
    registry.markErrored(broken.id);

    const ids = registry.listResumable(3600).map((session) => session.id);

    expect(ids).toContain(healthy.id);
    expect(ids).not.toContain(broken.id);
  });
});

describe("SessionRegistry.findUnprocessed", () => {
  it("selects both open and closed sessions whose postProcessingState is null", () => {
    const registry = new SessionRegistry(db);

    const open = registry.create("repl", "/tmp/open.jsonl");
    const closed = registry.create("repl", "/tmp/closed.jsonl");
    registry.close(closed.id);

    const ids = registry.findUnprocessed().map((session) => session.id);

    expect(ids).toEqual(expect.arrayContaining([open.id, closed.id]));
  });

  it("excludes sessions that completed post-processing (no backfill)", () => {
    const registry = new SessionRegistry(db);

    const processed = registry.create("repl", "/tmp/done.jsonl");
    registry.close(processed.id);
    registry.update(processed.id, { postProcessingState: { memory: "completed" } });

    expect(registry.findUnprocessed().map((session) => session.id)).not.toContain(processed.id);
  });
});
