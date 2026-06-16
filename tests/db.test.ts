import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";

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
