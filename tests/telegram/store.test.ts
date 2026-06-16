import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { TelegramMessageStore } from "../../src/extensions/telegram/store.ts";

let db: AppDatabase;
let store: TelegramMessageStore;

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-tg-store-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
  store = new TelegramMessageStore(db);
});

describe("TelegramMessageStore", () => {
  it("records a mapping and resolves the routing by message id", () => {
    store.record("m-1", { treeEntryId: "entry-1", branchId: "topic-3" }, "incoming");

    expect(store.resolve("m-1")).toEqual({ treeEntryId: "entry-1", branchId: "topic-3" });
  });

  it("upserts the mapping on conflict, repointing it to the latest routing", () => {
    store.record("m-1", { treeEntryId: "entry-1", branchId: "topic-1" }, "incoming");
    store.record("m-1", { treeEntryId: "entry-9", branchId: "topic-2" }, "outgoing");

    expect(store.resolve("m-1")).toEqual({ treeEntryId: "entry-9", branchId: "topic-2" });
  });

  it("returns null when no mapping exists for the message id", () => {
    expect(store.resolve("missing")).toBeNull();
  });

  it("records the live-branch id, which equals the branch's eventual collapse id", () => {
    // A message recorded mid-branch uses the same `topic-(count + 1)` formula collapse uses, so the
    // id written here is the id the branch carries once it later collapses.
    const liveBranchId = "topic-4";
    store.record("m-1", { treeEntryId: "entry-1", branchId: liveBranchId }, "outgoing");

    // A reply targeting that message later resolves to the same id the branch was assigned at collapse.
    expect(store.resolve("m-1")?.branchId).toBe(liveBranchId);
  });
});
