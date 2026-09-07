import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { eq, sql } from "drizzle-orm";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type AppDatabase, createDatabase, runMigrations } from "../../src/db/index.ts";
import { channelMessages } from "../../src/extensions/telegram/schema.ts";
import { TelegramMessageStore } from "../../src/extensions/telegram/store.ts";
import type { Logger } from "../../src/log.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

let db: AppDatabase;
let store: TelegramMessageStore;

/** The stored row for a message id — persistence details resolve() doesn't surface. */
const storedRow = (messageId: string) =>
  db.select().from(channelMessages).where(eq(channelMessages.messageId, messageId)).get();

beforeEach(async () => {
  const dir = await mkdtemp(join(tmpdir(), "tachi-tg-store-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
  store = new TelegramMessageStore(db, fakeLog);
});

describe("TelegramMessageStore", () => {
  it("records a mapping and resolves the routing by message id", () => {
    store.record("m-1", { treeEntryId: "entry-1", branchId: "topic-3" }, "incoming");

    expect(store.resolve("m-1")).toEqual({
      treeEntryId: "entry-1",
      branchId: "topic-3",
      label: null,
    });
  });

  it("upserts the mapping on conflict, repointing it to the latest routing", () => {
    store.record("m-1", { treeEntryId: "entry-1", branchId: "topic-1" }, "incoming");
    store.record("m-1", { treeEntryId: "entry-9", branchId: "topic-2" }, "outgoing");

    expect(store.resolve("m-1")).toMatchObject({ treeEntryId: "entry-9", branchId: "topic-2" });
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

describe("label (ledger content)", () => {
  it("stores the label and resolves it back", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", {
      label: "ship the fix tonight",
    });

    expect(store.resolve("m-1")?.label).toBe("ship the fix tonight");
  });

  it("normalizes a blank label to null", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "incoming", { label: "   " });
    store.record("m-2", { treeEntryId: "e1", branchId: "t1" }, "incoming", { label: "" });

    expect(store.resolve("m-1")?.label).toBeNull();
    expect(store.resolve("m-2")?.label).toBeNull();
  });

  it("truncates an over-long label with a head…tail ellipsis (280 bound)", () => {
    const long = `${"H".repeat(400)}${"T".repeat(400)}`;
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: long });

    const label = store.resolve("m-1")?.label ?? "";
    expect(label).toContain("…");
    expect(label.startsWith("H")).toBe(true);
    expect(label.endsWith("T")).toBe(true);
    expect(label.length).toBeLessThanOrEqual(280);
  });
});

describe("conditional conflict set", () => {
  it("overwrites the label when re-recorded with one", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: "first" });
    store.record("m-1", { treeEntryId: "e2", branchId: "t1" }, "outgoing", { label: "second" });

    expect(store.resolve("m-1")).toMatchObject({ label: "second", treeEntryId: "e2" });
  });

  it("preserves the label when re-recorded without one (no clobber)", () => {
    // The button-prompt scenario: the prompt row is written with a label; a later exchange
    // that references the same id (routing re-point) must not wipe it.
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: "Approve?" });
    store.record("m-1", { treeEntryId: "e2", branchId: "t2" }, "outgoing");

    expect(store.resolve("m-1")).toMatchObject({ label: "Approve?", treeEntryId: "e2" });
  });

  it("preserves inConversation when re-recorded without it", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", {
      inConversation: false,
    });
    store.record("m-1", { treeEntryId: "e2", branchId: "t1" }, "outgoing");

    expect(storedRow("m-1")?.inConversation).toBe(false);
  });

  it("overwrites inConversation when re-recorded with it", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing");
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", {
      inConversation: false,
    });

    expect(storedRow("m-1")?.inConversation).toBe(false);
  });

  it("defaults inConversation to true when omitted (insert)", () => {
    store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "incoming");

    expect(storedRow("m-1")?.inConversation).toBe(true);
  });
});

describe("isConversationBottom (bottom-of-entry test)", () => {
  it("marks the newest same-direction in-conversation row on an entry", () => {
    // One exchange, entry e1: inbound i1, outgoing chunks c1 then c2 (the final chunk).
    store.record("i1", { treeEntryId: "e1", branchId: "t1" }, "incoming");
    store.record("c1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: "chunk 1" });
    store.record("c2", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: "chunk 2" });

    expect(store.isConversationBottom("i1")).toBe(true);
    expect(store.isConversationBottom("c1")).toBe(false);
    expect(store.isConversationBottom("c2")).toBe(true);
  });

  it("is newest per direction — the inbound and the final chunk both qualify", () => {
    store.record("i1", { treeEntryId: "e1", branchId: "t1" }, "incoming");
    store.record("c1", { treeEntryId: "e1", branchId: "t1" }, "outgoing");
    store.record("i2", { treeEntryId: "e2", branchId: "t1" }, "incoming");

    expect(store.isConversationBottom("i1")).toBe(true);
  });

  it("excludes out-of-conversation rows even when recorded last on the entry", () => {
    // A delivery recorded between exchanges against the live leaf e1 — after the exchange's
    // rows — never qualifies as the conversation's bottom.
    store.record("i1", { treeEntryId: "e1", branchId: "t1" }, "incoming");
    store.record("c1", { treeEntryId: "e1", branchId: "t1" }, "outgoing");
    store.record("d1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", {
      label: "reminder",
      inConversation: false,
    });

    expect(store.isConversationBottom("d1")).toBe(false);
    expect(store.isConversationBottom("c1")).toBe(true);
  });

  it("returns false for an unrecorded message", () => {
    expect(store.isConversationBottom("missing")).toBe(false);
  });

  it("orders ties by row id when createdAt matches (same second)", () => {
    for (const id of ["c1", "c2", "c3"]) {
      store.record(id, { treeEntryId: "e1", branchId: "t1" }, "outgoing");
    }

    expect(store.isConversationBottom("c3")).toBe(true);
    expect(store.isConversationBottom("c2")).toBe(false);
  });
});

describe("recording failures", () => {
  it("never throws — a recording failure is logged and skipped", () => {
    db.run(sql`drop table channel_messages`);

    expect(() =>
      store.record("m-1", { treeEntryId: "e1", branchId: "t1" }, "outgoing", { label: "x" }),
    ).not.toThrow();
    expect(fakeLog.warn).toHaveBeenCalledWith(
      expect.objectContaining({ messageId: "m-1" }),
      "recording channel message failed",
    );
  });
});
