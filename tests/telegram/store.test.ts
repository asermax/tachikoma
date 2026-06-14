import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { TelegramMessageStore } from "../../src/extensions/telegram/store.ts";
import { SessionRegistry } from "../../src/sessions/registry.ts";

let db: AppDatabase;
let registry: SessionRegistry;
let store: TelegramMessageStore;

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-tg-store-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
  registry = new SessionRegistry(db);
  store = new TelegramMessageStore(db);
});

describe("TelegramMessageStore", () => {
  it("records a mapping and resolves the owning session by message id", () => {
    const session = registry.create("telegram", "/tmp/s.jsonl");
    store.record("m-1", session.id, "incoming");

    expect(store.findSessionId("m-1")).toBe(session.id);
  });

  it("upserts the mapping on conflict, repointing it to the latest session", () => {
    const first = registry.create("telegram", "/tmp/a.jsonl");
    const second = registry.create("telegram", "/tmp/b.jsonl");

    store.record("m-1", first.id, "incoming");
    store.record("m-1", second.id, "outgoing");

    expect(store.findSessionId("m-1")).toBe(second.id);
  });

  it("returns null when no mapping exists for the message id", () => {
    expect(store.findSessionId("missing")).toBeNull();
  });
});
