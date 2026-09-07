import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { eq, sql } from "drizzle-orm";
import { beforeEach, describe, expect, it } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import { channelMessages } from "../src/extensions/telegram/schema.ts";

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

describe("channel_messages ledger columns (0010)", () => {
  it("defaults a pre-ledger insert to in-conversation with no label", () => {
    // A row written the pre-ledger way (no label/in_conversation supplied) — the migration's
    // column defaults make it read back as an in-conversation row with a null label, so rows
    // from before the ledger exist behave as conversation rows, never out-of-band.
    db.run(sql`
      insert into channel_messages (channel, message_id, tree_entry_id, branch_id, direction, created_at)
      values ('telegram', 'm-1', 'e1', 'topic-1', 'outgoing', 0)
    `);

    const row = db.select().from(channelMessages).where(eq(channelMessages.messageId, "m-1")).get();

    expect(row?.label).toBeNull();
    expect(row?.inConversation).toBe(true);
  });
});
