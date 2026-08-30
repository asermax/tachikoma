import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { BranchSummaryDetails } from "../../src/agent/session-tree.ts";
import type { KeyValueState } from "../../src/db/state.ts";
import {
  BOOMERANG_STATE,
  BRANCH_SUMMARY,
  clearCheckpoint,
  clearLastAutoDecision,
  effectiveKind,
  getAllBranchRecords,
  getBranchRecords,
  isBranchAnalyzed,
  isBranchExtracted,
  isStepDone,
  localDay,
  markBranchAnalyzed,
  markBranchExtracted,
  markReversed,
  markStepDone,
  nextBranchId,
  nextTangentId,
  openOrCreateTrunk,
  readBoomerangState,
  recordLastAutoDecision,
  setCheckpoint,
  TrunkState,
  writeBoomerangState,
} from "../../src/sessions/trunk.ts";

const h = vi.hoisted(() => ({ existsSync: vi.fn(() => true) }));

vi.mock("node:fs", () => ({
  existsSync: (...args: unknown[]) => h.existsSync(...args),
}));

// A KeyValueState-shaped fake backed by a plain map; values are stored by reference, matching the
// real store's JSON round-trip closely enough for these tests.
const makeState = (): KeyValueState => {
  const store = new Map<string, unknown>();

  return {
    get: <T>(key: string): T | null => (store.has(key) ? (store.get(key) as T) : null),
    set: <T>(key: string, value: T): void => {
      store.set(key, value);
    },
    delete: (key: string): void => {
      store.delete(key);
    },
  } as unknown as KeyValueState;
};

let entryCounter = 0;

const messageEntry = (): SessionEntry => {
  entryCounter += 1;
  return {
    type: "message",
    id: `m-${entryCounter}`,
    parentId: null,
    timestamp: "2026-06-15T00:00:00.000Z",
    message: { role: "user", content: [] },
  } as unknown as SessionEntry;
};

const branchSummaryEntry = (id: string, details: BranchSummaryDetails): SessionEntry =>
  ({
    type: "branch_summary",
    id,
    parentId: null,
    timestamp: "2026-06-15T00:00:00.000Z",
    fromId: details.baseId ?? "root",
    summary: "summary",
    details,
    fromHook: true,
  }) as unknown as SessionEntry;

// A fake AgentSession whose sessionManager exposes an append-only entries list, getEntry, getLeafId,
// and a branch() that records the re-seated leaf. No LLM/prompt is ever involved.
const makeSession = (sessionFile: string | undefined, initial: SessionEntry[] = []) => {
  const entries = [...initial];
  let leafId: string | null = entries.length > 0 ? entries[entries.length - 1].id : null;
  let customCounter = 0;

  const appendCustom = (customType: string, data: unknown): string => {
    customCounter += 1;
    const id = `c-${customCounter}`;
    entries.push({
      type: "custom",
      id,
      parentId: leafId,
      timestamp: "2026-06-15T00:00:00.000Z",
      customType,
      data,
    } as unknown as SessionEntry);
    leafId = id;
    return id;
  };

  return {
    sessionFile,
    sessionManager: {
      getEntries: () => [...entries],
      getEntry: (id: string) => entries.find((entry) => entry.id === id),
      getLeafId: () => leafId,
      branch: (id: string) => {
        leafId = id;
      },
      appendCustomEntry: appendCustom,
      branchFromId: () => leafId,
    },
    _entries: entries,
    _leaf: () => leafId,
  } as unknown as AgentSession & { _leaf: () => string | null };
};

/** Append a boomerang snapshot with arbitrary (partial) data, simulating an older file shape. */
const appendRawBoomerang = (session: AgentSession, data: unknown): void => {
  session.sessionManager.appendCustomEntry(BOOMERANG_STATE, data);
};

beforeEach(() => {
  entryCounter = 0;
  h.existsSync.mockReset();
  h.existsSync.mockReturnValue(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TrunkState pointer + unclosed index", () => {
  it("round-trips the active pointer and clears it", () => {
    const trunk = new TrunkState(makeState());

    expect(trunk.getActive()).toBeNull();

    const pointer = { sessionFile: "/s/a.jsonl", day: "2026-06-15", openedAt: "t" };
    trunk.setActive(pointer);
    expect(trunk.getActive()).toEqual(pointer);

    trunk.clearActive();
    expect(trunk.getActive()).toBeNull();
  });

  it("adds and removes unclosed files without duplicates", () => {
    const trunk = new TrunkState(makeState());

    expect(trunk.listUnclosed()).toEqual([]);

    trunk.addUnclosed("/s/a.jsonl");
    trunk.addUnclosed("/s/a.jsonl");
    trunk.addUnclosed("/s/b.jsonl");
    expect(trunk.listUnclosed()).toEqual(["/s/a.jsonl", "/s/b.jsonl"]);

    trunk.removeUnclosed("/s/a.jsonl");
    expect(trunk.listUnclosed()).toEqual(["/s/b.jsonl"]);
  });

  it("promoteToActive adds to unclosed BEFORE setting active (write-ordering invariant)", () => {
    const order: string[] = [];
    const state = {
      get: () => null,
      set: (key: string) => {
        order.push(key);
      },
      delete: () => {},
    } as unknown as KeyValueState;
    const trunk = new TrunkState(state);

    trunk.promoteToActive({ sessionFile: "/s/a.jsonl", day: "2026-06-15", openedAt: "t" });

    expect(order).toEqual(["unclosed", "active"]);
  });

  it("retireTrunk removes from unclosed only, leaving active untouched", () => {
    const trunk = new TrunkState(makeState());
    trunk.promoteToActive({ sessionFile: "/s/a.jsonl", day: "2026-06-15", openedAt: "t" });

    trunk.retireTrunk("/s/a.jsonl");

    expect(trunk.listUnclosed()).toEqual([]);
    expect(trunk.getActive()).not.toBeNull();
  });
});

describe("localDay", () => {
  it("computes the day in the injected timezone", () => {
    // 2026-06-15T23:30:00Z is still the 15th in UTC but already the 16th in Tokyo (+09:00).
    const instant = () => new Date("2026-06-15T23:30:00.000Z");

    expect(localDay(instant, "UTC")).toBe("2026-06-15");
    expect(localDay(instant, "Asia/Tokyo")).toBe("2026-06-16");
  });

  it("rolls back a day for a negative-offset timezone near midnight", () => {
    // 2026-06-16T02:00:00Z is the 16th in UTC but still the 15th in New York (-04:00 in June).
    const instant = () => new Date("2026-06-16T02:00:00.000Z");

    expect(localDay(instant, "UTC")).toBe("2026-06-16");
    expect(localDay(instant, "America/New_York")).toBe("2026-06-15");
  });
});

describe("openOrCreateTrunk", () => {
  it("creates a fresh trunk and promotes it (invariant order) when no pointer exists", async () => {
    const order: string[] = [];
    const state = {
      get: () => null,
      set: (key: string) => {
        order.push(key);
      },
      delete: () => {},
    } as unknown as KeyValueState;
    const trunk = new TrunkState(state);

    const session = makeSession("/s/new.jsonl");
    const open = vi.fn().mockResolvedValue(session);

    const result = await openOrCreateTrunk(
      { agent: { open }, trunk, now: () => new Date("2026-06-15T12:00:00Z"), timezone: "UTC" },
      "2026-06-15",
    );

    expect(open).toHaveBeenCalledWith({});
    expect(result.isNew).toBe(true);
    expect(result.sessionFile).toBe("/s/new.jsonl");
    expect(order).toEqual(["unclosed", "active"]);
  });

  it("creates fresh when the pointer's day is stale", async () => {
    const trunk = new TrunkState(makeState());
    trunk.promoteToActive({ sessionFile: "/s/old.jsonl", day: "2026-06-14", openedAt: "t" });

    const session = makeSession("/s/new.jsonl");
    const open = vi.fn().mockResolvedValue(session);

    const result = await openOrCreateTrunk(
      { agent: { open }, trunk, now: () => new Date(), timezone: "UTC" },
      "2026-06-15",
    );

    expect(open).toHaveBeenCalledWith({});
    expect(result.isNew).toBe(true);
  });

  it("same-day reopen re-seats the leaf onto the current base, not the last file entry", async () => {
    const base = messageEntry();
    const summary = branchSummaryEntry("sum-1", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-1",
      originalLeafId: base.id,
      baseId: null,
    });
    // A later entry exists after the summary; pi's open would seat the leaf here — we must not.
    const strayLeaf = messageEntry();

    const session = makeSession("/s/today.jsonl", [base, summary, strayLeaf]);
    expect(session._leaf()).toBe(strayLeaf.id);

    const trunk = new TrunkState(makeState());
    trunk.promoteToActive({ sessionFile: "/s/today.jsonl", day: "2026-06-15", openedAt: "t" });

    const open = vi.fn().mockResolvedValue(session);

    const result = await openOrCreateTrunk(
      { agent: { open }, trunk, now: () => new Date(), timezone: "UTC" },
      "2026-06-15",
    );

    expect(open).toHaveBeenCalledWith({ sessionFile: "/s/today.jsonl" });
    expect(result.isNew).toBe(false);
    expect(session._leaf()).toBe("sum-1");
  });

  it("re-seats onto the boomerang snapshot base when present", async () => {
    const base = messageEntry();
    const summary = branchSummaryEntry("sum-1", {
      customType: BRANCH_SUMMARY,
      branchId: "topic-1",
      originalLeafId: base.id,
      baseId: null,
    });
    const session = makeSession("/s/today.jsonl", [base, summary]);
    writeBoomerangState(session, {
      currentTopicBaseId: "sum-1",
      lastDecision: "shift",
      relatedBranchId: null,
      checkpointId: null,
      lastAutoDecision: null,
    });

    const trunk = new TrunkState(makeState());
    trunk.promoteToActive({ sessionFile: "/s/today.jsonl", day: "2026-06-15", openedAt: "t" });

    const open = vi.fn().mockResolvedValue(session);
    await openOrCreateTrunk(
      { agent: { open }, trunk, now: () => new Date(), timezone: "UTC" },
      "2026-06-15",
    );

    expect(session._leaf()).toBe("sum-1");
  });

  it("creates fresh when the pointer day matches but the file is gone", async () => {
    h.existsSync.mockReturnValue(false);

    const trunk = new TrunkState(makeState());
    trunk.promoteToActive({ sessionFile: "/s/today.jsonl", day: "2026-06-15", openedAt: "t" });

    const session = makeSession("/s/new.jsonl");
    const open = vi.fn().mockResolvedValue(session);

    const result = await openOrCreateTrunk(
      { agent: { open }, trunk, now: () => new Date(), timezone: "UTC" },
      "2026-06-15",
    );

    expect(open).toHaveBeenCalledWith({});
    expect(result.isNew).toBe(true);
  });
});

describe("getBranchRecords", () => {
  const buildThreeBranchTrunk = () => {
    const leaf1 = messageEntry();
    const leaf2 = messageEntry();
    const leaf3 = messageEntry();

    const entries = [
      leaf1,
      branchSummaryEntry("sum-1", {
        customType: BRANCH_SUMMARY,
        branchId: "topic-1",
        originalLeafId: leaf1.id,
        baseId: null,
        lastExchange: "ex-1",
      }),
      leaf2,
      branchSummaryEntry("sum-2", {
        customType: BRANCH_SUMMARY,
        branchId: "topic-2",
        originalLeafId: leaf2.id,
        baseId: "sum-1",
        lastExchange: "ex-2",
      }),
      leaf3,
      branchSummaryEntry("sum-3", {
        customType: BRANCH_SUMMARY,
        branchId: "topic-3",
        originalLeafId: leaf3.id,
        baseId: "sum-2",
        lastExchange: "ex-3",
      }),
    ];

    return makeSession("/s/today.jsonl", entries);
  };

  it("derives deterministic topic-N ids stable across two rebuilds", async () => {
    const session = buildThreeBranchTrunk();

    const first = getBranchRecords(session);
    const second = getBranchRecords(session);

    expect(first.map((record) => record.branchId)).toEqual(["topic-1", "topic-2", "topic-3"]);
    expect(second).toEqual(first);
    expect(first[1]).toMatchObject({
      branchId: "topic-2",
      summaryEntryId: "sum-2",
      baseId: "sum-1",
      lastExchange: "ex-2",
    });
  });

  it("drops a record whose referenced ids do not resolve", () => {
    const session = makeSession("/s/today.jsonl", [
      branchSummaryEntry("sum-1", {
        customType: BRANCH_SUMMARY,
        branchId: "topic-1",
        originalLeafId: "missing-leaf",
        baseId: null,
      }),
    ]);

    expect(getBranchRecords(session)).toEqual([]);
  });
});

describe("boomerang state", () => {
  it("returns the latest snapshot on the path", () => {
    const session = makeSession("/s/today.jsonl");

    expect(readBoomerangState(session)).toBeNull();

    writeBoomerangState(session, {
      currentTopicBaseId: "a",
      lastDecision: "continue",
      relatedBranchId: null,
      checkpointId: null,
      lastAutoDecision: null,
    });
    writeBoomerangState(session, {
      currentTopicBaseId: "b",
      lastDecision: "shift",
      relatedBranchId: "topic-1",
      checkpointId: null,
      lastAutoDecision: null,
    });

    expect(readBoomerangState(session)).toEqual({
      currentTopicBaseId: "b",
      lastDecision: "shift",
      relatedBranchId: "topic-1",
      checkpointId: null,
      lastAutoDecision: null,
    });
  });

  it("normalizes a pre-delta snapshot lacking checkpoint/lastAutoDecision (backward-compat)", () => {
    // A snapshot written before DLT-181 carries only the original three fields.
    const session = makeSession("/s/today.jsonl");
    appendRawBoomerang(session, {
      currentTopicBaseId: "sum-1",
      lastDecision: "shift",
      relatedBranchId: null,
    });

    expect(readBoomerangState(session)).toEqual({
      currentTopicBaseId: "sum-1",
      lastDecision: "shift",
      relatedBranchId: null,
      checkpointId: null,
      lastAutoDecision: null,
    });
  });
});

describe("completion markers", () => {
  it("tracks per-branch extraction markers independently", () => {
    const session = makeSession("/s/today.jsonl");

    expect(isBranchExtracted(session, "topic-1")).toBe(false);

    markBranchExtracted(session, "topic-1");

    expect(isBranchExtracted(session, "topic-1")).toBe(true);
    expect(isBranchExtracted(session, "topic-2")).toBe(false);
  });

  it("tracks per-step markers independently of branch markers", () => {
    const session = makeSession("/s/today.jsonl");

    expect(isStepDone(session, "consolidate")).toBe(false);

    markStepDone(session, "consolidate");
    markBranchExtracted(session, "consolidate");

    expect(isStepDone(session, "consolidate")).toBe(true);
    expect(isStepDone(session, "prune")).toBe(false);
    // a branch marker named like a step must not satisfy the step query
    expect(isStepDone(session, "topic-9")).toBe(false);
  });

  it("round-trips skill-evolution analyzed markers keyed by summaryEntryId (DLT-080)", () => {
    const session = makeSession("/s/today.jsonl");

    expect(isBranchAnalyzed(session, "sum-1")).toBe(false);

    markBranchAnalyzed(session, "sum-1");

    expect(isBranchAnalyzed(session, "sum-1")).toBe(true);
    expect(isBranchAnalyzed(session, "sum-2")).toBe(false);
  });

  it("isolates analyzed markers from extracted and step markers (cross-kind)", () => {
    const session = makeSession("/s/today.jsonl");

    // An `extracted` marker carrying the same string must not satisfy the analyzed query…
    markBranchExtracted(session, "sum-1");
    expect(isBranchAnalyzed(session, "sum-1")).toBe(false);

    // …and vice versa, even when the strings look like the other kind's key.
    markBranchAnalyzed(session, "topic-1");
    markBranchAnalyzed(session, "memory-prune");

    expect(isBranchExtracted(session, "topic-1")).toBe(false);
    expect(isStepDone(session, "memory-prune")).toBe(false);
  });
});

describe("branch kind discriminator (DLT-181)", () => {
  const topicSummary = (id: string, leafId: string, baseId: string | null = null): SessionEntry =>
    branchSummaryEntry(id, {
      customType: BRANCH_SUMMARY,
      branchId: "topic-x",
      kind: "topic",
      originalLeafId: leafId,
      baseId,
    });

  const tangentSummary = (id: string, leafId: string, baseId: string): SessionEntry =>
    branchSummaryEntry(id, {
      customType: BRANCH_SUMMARY,
      branchId: "tangent-x",
      kind: "tangent",
      originalLeafId: leafId,
      baseId,
    });

  it("getBranchRecords excludes tangents and reversed summaries, keeping topic-N clean (KD3/KD6)", () => {
    const leaf1 = messageEntry();
    const tangentLeaf = messageEntry();
    const leaf2 = messageEntry();
    const session = makeSession("/s/today.jsonl", [
      leaf1,
      topicSummary("sum-1", leaf1.id),
      tangentLeaf,
      tangentSummary("tan-1", tangentLeaf.id, "sum-1"),
      leaf2,
      topicSummary("sum-2", leaf2.id, "sum-1"),
    ]);

    // Only the two topics are enumerated; the tangent is parked away (KD3/KD7).
    expect(getBranchRecords(session).map((record) => record.summaryEntryId)).toEqual([
      "sum-1",
      "sum-2",
    ]);
    // nextBranchId counts only topics, so a tangent in the middle does not perturb topic-N.
    expect(nextBranchId(getBranchRecords(session))).toBe("topic-3");
  });

  it("getAllBranchRecords returns every summary including tangents (internal callers)", () => {
    const leaf1 = messageEntry();
    const tangentLeaf = messageEntry();
    const session = makeSession("/s/today.jsonl", [
      leaf1,
      topicSummary("sum-1", leaf1.id),
      tangentLeaf,
      tangentSummary("tan-1", tangentLeaf.id, "sum-1"),
    ]);

    expect(getAllBranchRecords(session).map((record) => record.summaryEntryId)).toEqual([
      "sum-1",
      "tan-1",
    ]);
  });

  it("nextTangentId advances on its own sequence, independent of topic-N", () => {
    const leaf1 = messageEntry();
    const t1 = messageEntry();
    const session = makeSession("/s/today.jsonl", [
      leaf1,
      topicSummary("sum-1", leaf1.id),
      t1,
      tangentSummary("tan-1", t1.id, "sum-1"),
    ]);

    expect(nextTangentId(session)).toBe("tangent-2");
  });

  it("effectiveKind reports reversed for a markered summary, tangent for tangents, topic otherwise", () => {
    const leaf1 = messageEntry();
    const tangentLeaf = messageEntry();
    const reversedLeaf = messageEntry();
    const session = makeSession("/s/today.jsonl", [
      leaf1,
      topicSummary("sum-1", leaf1.id),
      tangentLeaf,
      tangentSummary("tan-1", tangentLeaf.id, "sum-1"),
      reversedLeaf,
      topicSummary("sum-2", reversedLeaf.id, "sum-1"),
    ]);

    expect(effectiveKind(session, "sum-1", "topic")).toBe("topic");
    expect(effectiveKind(session, "tan-1", "tangent")).toBe("tangent");

    markReversed(session, "sum-2");
    expect(effectiveKind(session, "sum-2", "topic")).toBe("reversed");
    // A reversed summary drops out of the topic enumeration too (KD6).
    expect(getBranchRecords(session).map((record) => record.summaryEntryId)).toEqual(["sum-1"]);
  });
});

describe("checkpoint + decision-log lifecycle (DLT-181)", () => {
  it("setCheckpoint records the tip and overrides a prior checkpoint (R2)", () => {
    const session = makeSession("/s/today.jsonl");

    setCheckpoint(session, "leaf-1");
    expect(readBoomerangState(session)?.checkpointId).toBe("leaf-1");

    setCheckpoint(session, "leaf-2");
    expect(readBoomerangState(session)?.checkpointId).toBe("leaf-2");
  });

  it("clearCheckpoint nulls the checkpoint, preserving the rest of the snapshot", () => {
    const session = makeSession("/s/today.jsonl");
    setCheckpoint(session, "leaf-1");
    recordLastAutoDecision(session, "new", "leaf-0");

    clearCheckpoint(session);

    const state = readBoomerangState(session);
    expect(state?.checkpointId).toBeNull();
    expect(state?.lastAutoDecision).toEqual({ kind: "new", preDecisionLeafId: "leaf-0" });
  });

  it("recordLastAutoDecision/clearLastAutoDecision round-trip", () => {
    const session = makeSession("/s/today.jsonl");

    recordLastAutoDecision(session, "set-checkpoint", "leaf-0");
    expect(readBoomerangState(session)?.lastAutoDecision).toEqual({
      kind: "set-checkpoint",
      preDecisionLeafId: "leaf-0",
    });

    clearLastAutoDecision(session);
    expect(readBoomerangState(session)?.lastAutoDecision).toBeNull();
  });
});
