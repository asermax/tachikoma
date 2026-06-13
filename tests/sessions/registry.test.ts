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
