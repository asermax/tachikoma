import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import { getBranchEntries } from "../../src/agent/session-tree.ts";
import type { DecisionHeader } from "../../src/domain/message.ts";
import { textMessage } from "../../src/domain/message.ts";
import type { TrunkInbound } from "../../src/extensions/api.ts";
import { handleRollbackCommand } from "../../src/extensions/boundary/rollback.ts";
import type { Logger } from "../../src/log.ts";
import {
  BRANCH_SUMMARY,
  effectiveKind,
  getBranchRecords,
  readBoomerangState,
  recordLastAutoDecision,
  setCheckpoint,
  writeBoomerangState,
} from "../../src/sessions/trunk.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

const TIMESTAMP = "2026-06-15T00:00:00Z";

const messageEntry = (id: string, role: "user" | "assistant", text: string): SessionEntry =>
  ({
    type: "message",
    id,
    parentId: null,
    timestamp: TIMESTAMP,
    message: { role, content: [{ type: "text", text }] },
  }) as unknown as SessionEntry;

interface BranchCall {
  branchFromId: string | null;
  summary: string;
  details: unknown;
  returnedId: string;
}

interface FakeSession {
  session: AgentSession;
  branchCalls: BranchCall[];
  branchReseats: string[];
  appendMessage: (entry: SessionEntry) => void;
  leaf: () => string | null;
}

/**
 * A faithful fake session: `appendCustomEntry`/`branchWithSummary` advance/re-seat the leaf exactly as
 * real pi does (`_appendEntry` sets `this.leafId`), `branch(id)` re-seats the leaf backward (the
 * sanctioned rewind — off-path entries stay in the index), and `getBranch` walks `parentId` from the
 * leaf — so a rewind genuinely moves the wrong-framing exchange off-path. This is what makes the
 * immediacy walk, the off-path check, and the reversed-summary exclusion meaningful.
 */
const makeSession = (initial: SessionEntry[] = []): FakeSession => {
  const byId = new Map<string, SessionEntry>();
  const order: SessionEntry[] = [];
  let leafId: string | null = null;
  let counter = 0;
  const branchCalls: BranchCall[] = [];
  const branchReseats: string[] = [];

  for (const entry of initial) {
    byId.set(entry.id, entry);
    order.push(entry);
  }
  leafId = initial.at(-1)?.id ?? null;

  const nextId = (prefix: string): string => {
    counter += 1;
    return `${prefix}-${counter}`;
  };

  const appendAt = (entry: SessionEntry, parentId: string | null): void => {
    (entry as { parentId: string | null }).parentId = parentId;
    byId.set(entry.id, entry);
    order.push(entry);
    leafId = entry.id;
  };

  const getBranch = (fromId?: string): SessionEntry[] => {
    const startId = fromId ?? leafId;
    const path: SessionEntry[] = [];
    let cur = startId != null ? (byId.get(startId) ?? null) : null;
    while (cur) {
      path.unshift(cur);
      const pid = (cur as { parentId?: string | null }).parentId;
      cur = pid != null ? (byId.get(pid) ?? null) : null;
    }
    return path;
  };

  const sessionManager = {
    getEntries: () => [...order],
    getEntry: (id: string) => byId.get(id),
    getLeafId: () => leafId,
    getBranch,
    branch: vi.fn((id: string) => {
      leafId = id;
      branchReseats.push(id);
    }),
    branchWithSummary: vi.fn((branchFromId: string | null, summary: string, details: unknown) => {
      const id = nextId("sum");
      branchCalls.push({ branchFromId, summary, details, returnedId: id });
      appendAt(
        {
          type: "branch_summary",
          id,
          parentId: null,
          timestamp: TIMESTAMP,
          summary,
          details,
          fromHook: true,
        } as unknown as SessionEntry,
        branchFromId,
      );
      return id;
    }),
    appendCustomEntry: vi.fn((customType: string, data: unknown) => {
      const id = nextId("c");
      appendAt(
        {
          type: "custom",
          id,
          parentId: null,
          timestamp: TIMESTAMP,
          customType,
          data,
        } as unknown as SessionEntry,
        leafId,
      );
      return id;
    }),
  };

  return {
    session: { sessionManager } as unknown as AgentSession,
    branchCalls,
    branchReseats,
    appendMessage: (entry: SessionEntry) => appendAt(entry, leafId),
    leaf: () => leafId,
  };
};

const msg = (text: string) => textMessage("test", text);

const deps = (replay: ReturnType<typeof vi.fn> = vi.fn()) => ({
  side: { complete: vi.fn().mockResolvedValue("a topic summary") },
  log: fakeLog,
  deliver: vi.fn(),
  replay,
});

const trunkFrom = (fake: FakeSession, overrides: Partial<TrunkInbound> = {}): TrunkInbound => {
  const boomerang = readBoomerangState(fake.session);
  return {
    session: fake.session,
    sessionFile: "/tmp/trunk.jsonl",
    currentBaseId: boomerang?.currentTopicBaseId ?? null,
    branchRecords: getBranchRecords(fake.session),
    liveBranchId: `topic-${getBranchRecords(fake.session).length + 1}`,
    hasAssistantTurnSinceBase: true,
    checkpointId: boomerang?.checkpointId ?? null,
    lastAutoDecision: boomerang?.lastAutoDecision ?? null,
    ...overrides,
  };
};

/**
 * Stage an automatic set-checkpoint decision at `checkpointTip`: write the boomerang base, record the
 * decision, set the checkpoint, then append the triggering tangent exchange (M + answer). Mirrors what
 * the Batch-3 auto path leaves on the tree.
 */
const stageAutoSetCheckpoint = (
  fake: FakeSession,
  baseId: string | null,
  checkpointTip: string,
  triggeringText: string,
): void => {
  writeBoomerangState(fake.session, {
    currentTopicBaseId: baseId,
    lastDecision: "continue",
    relatedBranchId: null,
    checkpointId: null,
    lastAutoDecision: null,
  });
  recordLastAutoDecision(fake.session, "set-checkpoint", checkpointTip);
  setCheckpoint(fake.session, checkpointTip);
  // The triggering exchange lands after the boomerang appends (the inline tangent's first turn).
  fake.appendMessage(messageEntry("m-trig", "user", triggeringText));
  fake.appendMessage(messageEntry("m-resp", "assistant", "tangent answer"));
};

/**
 * Stage an automatic `new` (topic shift) decision: collapse the branch up to `preDecisionLeaf` into a
 * topic summary rooted at `baseId`, record the decision, then append the triggering exchange (the new
 * topic's first turn). Mirrors what the Batch-3 auto-shift path leaves on the tree.
 */
const stageAutoNew = (
  fake: FakeSession,
  baseId: string | null,
  preDecisionLeaf: string,
  triggeringText: string,
): string => {
  // collapse up to preDecisionLeaf into a topic summary rooted at baseId (the auto-new).
  const summaryId = fake.session.sessionManager.branchWithSummary(baseId, "prior topic summary", {
    customType: BRANCH_SUMMARY,
    branchId: "topic-1",
    kind: "topic",
    originalLeafId: preDecisionLeaf,
    baseId,
  }) as string;
  writeBoomerangState(fake.session, {
    currentTopicBaseId: summaryId,
    lastDecision: "shift",
    relatedBranchId: null,
    checkpointId: null,
    lastAutoDecision: null,
  });
  recordLastAutoDecision(fake.session, "new", preDecisionLeaf);
  // The triggering exchange lands as the first turn of the new topic.
  fake.appendMessage(messageEntry("m-trig", "user", triggeringText));
  fake.appendMessage(messageEntry("m-resp", "assistant", "new topic answer"));
  return summaryId;
};

describe("handleRollbackCommand (/rollback) — eligibility + no-ops", () => {
  it("returns false (not handled) for a non-/rollback message", async () => {
    const fake = makeSession([messageEntry("m1", "user", "hi")]);
    const replay = vi.fn();

    const handled = await handleRollbackCommand(deps(replay), msg("hello"), trunkFrom(fake));

    expect(handled).toBe(false);
    expect(replay).not.toHaveBeenCalled();
  });

  it("no-ops with a notice when there is no recent auto decision (R7/R11)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hey"),
    ]);
    const replay = vi.fn();
    const deliver = vi.fn();

    const handled = await handleRollbackCommand(
      { ...deps(replay), deliver },
      msg("/rollback"),
      trunkFrom(fake),
    );

    expect(handled).toBe(true);
    expect(msg("/rollback").metadata.handled ?? true).toBe(true);
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("Nothing to roll back"),
        immediate: true,
      }),
    );
    expect(replay).not.toHaveBeenCalled();
    expect(fake.branchReseats).toHaveLength(0);
  });

  it("no-ops when a later user turn happened since the decision (not immediate)", async () => {
    const fake = makeSession([
      messageEntry("base", "assistant", "main line"),
      messageEntry("c", "assistant", "checkpoint tip"),
    ]);
    stageAutoSetCheckpoint(fake, "base", "c", "original side question");
    // A second user exchange happened after the triggering one → not immediate.
    fake.appendMessage(messageEntry("m2", "user", "a follow-up turn"));
    fake.appendMessage(messageEntry("m2r", "assistant", "follow-up answer"));

    const replay = vi.fn();
    const deliver = vi.fn();
    await handleRollbackCommand({ ...deps(replay), deliver }, msg("/rollback"), trunkFrom(fake));

    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("Nothing to roll back") }),
    );
    expect(replay).not.toHaveBeenCalled();
    expect(fake.branchReseats).toHaveLength(0);
  });

  it("no-ops when the checkpoint was since summarized away (stale, restart-safe)", async () => {
    const fake = makeSession([
      messageEntry("base", "assistant", "main line"),
      messageEntry("c", "assistant", "checkpoint tip"),
    ]);
    stageAutoSetCheckpoint(fake, "base", "c", "side question");
    // A summarize-to-checkpoint folded the tangent away (no new user turn) — checkpointId cleared and
    // the tangent summary re-seats the leaf, so no user turn follows the decision marker anymore.
    fake.session.sessionManager.branchWithSummary("c", "tangent summary", {
      customType: BRANCH_SUMMARY,
      branchId: "tangent-1",
      kind: "tangent",
      originalLeafId: "m-resp",
      baseId: "c",
    });
    writeBoomerangState(fake.session, {
      ...(readBoomerangState(fake.session) ?? {
        currentTopicBaseId: null,
        lastDecision: null,
        relatedBranchId: null,
        checkpointId: null,
        lastAutoDecision: null,
      }),
      checkpointId: null,
    });

    const replay = vi.fn();
    const deliver = vi.fn();
    await handleRollbackCommand({ ...deps(replay), deliver }, msg("/rollback"), trunkFrom(fake));

    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("Nothing to roll back") }),
    );
    expect(replay).not.toHaveBeenCalled();
  });
});

describe("handleRollbackCommand — Case A (set-checkpoint → topic)", () => {
  it("rewinds, collapses the branch as a topic, clears the decision, and replays (R7a)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main topic start"),
      messageEntry("base-a", "assistant", "main line"),
      messageEntry("c", "assistant", "the checkpoint tip"),
    ]);
    stageAutoSetCheckpoint(fake, null, "c", "actually a whole new topic");

    const replay = vi.fn();
    const handled = await handleRollbackCommand(deps(replay), msg("/rollback"), trunkFrom(fake));

    expect(handled).toBe(true);
    // Rewound to the pre-decision tip (the checkpoint tip).
    expect(fake.branchReseats).toContain("c");
    // The branch up to the tip collapsed as a TOPIC summary (Case A's opposite transition).
    expect(fake.branchCalls).toHaveLength(1);
    expect(fake.branchCalls[0]?.details).toMatchObject({ kind: "topic" });
    expect(fake.branchCalls[0]?.branchFromId).toBeNull(); // base was null (first topic)
    // The decision log is cleared.
    expect(readBoomerangState(fake.session)?.lastAutoDecision).toBeNull();
    // The checkpoint was invalidated by the topic collapse.
    expect(readBoomerangState(fake.session)?.checkpointId).toBeNull();
    // The triggering message is replayed under the topic framing with the rollback header.
    expect(replay).toHaveBeenCalledTimes(1);
    const [replayedText, header] = replay.mock.calls[0] ?? [];
    expect(replayedText).toBe("actually a whole new topic");
    expect(header).toMatchObject({
      label: "🔄 Rolled back to topic",
      rollbackable: false,
    });
  });

  it("the wrong-framing tangent exchange goes off-path after the rewind (append-only, KD4)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main"),
      messageEntry("base-a", "assistant", "line"),
      messageEntry("c", "assistant", "tip"),
    ]);
    stageAutoSetCheckpoint(fake, null, "c", "new topic really");

    await handleRollbackCommand(deps(vi.fn()), msg("/rollback"), trunkFrom(fake));

    // After rewind the triggering exchange + its tangent answer are off the active path. In Case A the
    // pre-decision tip "c" is ALSO off-path: it is folded into the topic summary (the branch up to it
    // collapses — exactly a topic shift) and recorded as that summary's abandoned leaf, so the replayed
    // message extends the new summary instead. The wrong-framing entries stay in the tree (KD4:
    // append-only — off-path, never deleted).
    const path = getBranchEntries(fake.session).map((e) => e.id);
    expect(path).not.toContain("m-trig");
    expect(path).not.toContain("m-resp");
    expect(fake.branchCalls[0]?.details).toMatchObject({ originalLeafId: "c", kind: "topic" });
    const allIds = new Set(fake.session.sessionManager.getEntries().map((entry) => entry.id));
    expect(allIds.has("m-trig")).toBe(true);
    expect(allIds.has("m-resp")).toBe(true);
  });
});

describe("handleRollbackCommand — Case B (new → checkpoint)", () => {
  it("rewinds, sets a checkpoint, marks the auto-new summary reversed, fixes the base, replays (R7b)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main topic"),
      messageEntry("base-a", "assistant", "main line"),
      messageEntry("p", "assistant", "pre-decision tip"),
    ]);
    const summaryId = stageAutoNew(fake, null, "p", "actually just a side question");

    const replay = vi.fn();
    const handled = await handleRollbackCommand(deps(replay), msg("/rollback"), trunkFrom(fake));

    expect(handled).toBe(true);
    // Rewound to the pre-decision tip (the auto-new's abandoned leaf).
    expect(fake.branchReseats).toContain("p");
    // A checkpoint is set at the restored tip; the base is restored to what the reversed shift extended.
    const boomerang = readBoomerangState(fake.session);
    expect(boomerang?.checkpointId).toBe("p");
    expect(boomerang?.currentTopicBaseId).toBeNull(); // the auto-new's base (null — first topic)
    expect(boomerang?.lastAutoDecision).toBeNull();
    // The auto-new's topic summary is orphaned: effective kind "reversed", excluded from topic records.
    expect(summaryId).toBeTruthy();
    expect(effectiveKind(fake.session, summaryId, "topic")).toBe("reversed");
    expect(getBranchRecords(fake.session).map((r) => r.summaryEntryId)).not.toContain(summaryId);
    // The triggering message is replayed under the checkpoint framing with the rollback header.
    expect(replay).toHaveBeenCalledTimes(1);
    const [replayedText, header] = replay.mock.calls[0] ?? [];
    expect(replayedText).toBe("actually just a side question");
    expect(header).toMatchObject({
      label: "🔄 Rolled back to checkpoint",
      rollbackable: false,
    });
  });

  it("restores the prior topic base so a reopen would not re-seat onto the reversed summary", async () => {
    // Prior topic summary `sum-base` is the base; the auto-new collapsed a later branch off it.
    const fake = makeSession([
      messageEntry("root", "user", "first"),
      {
        type: "branch_summary",
        id: "sum-base",
        parentId: "root",
        timestamp: TIMESTAMP,
        summary: "first topic",
        details: {
          customType: BRANCH_SUMMARY,
          branchId: "topic-1",
          kind: "topic",
          originalLeafId: "root",
          baseId: null,
        },
        fromHook: true,
      } as unknown as SessionEntry,
      messageEntry("p", "assistant", "pre-decision tip"),
    ]);
    stageAutoNew(fake, "sum-base", "p", "a side question");

    await handleRollbackCommand(deps(vi.fn()), msg("/rollback"), trunkFrom(fake));

    // currentTopicBaseId is restored to the prior topic summary, not left on the reversed one.
    expect(readBoomerangState(fake.session)?.currentTopicBaseId).toBe("sum-base");
  });

  it("the wrong-framing new-topic exchange goes off-path after the rewind (KD4)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main"),
      messageEntry("base-a", "assistant", "line"),
      messageEntry("p", "assistant", "tip"),
    ]);
    stageAutoNew(fake, null, "p", "side q");

    await handleRollbackCommand(deps(vi.fn()), msg("/rollback"), trunkFrom(fake));

    const path = getBranchEntries(fake.session).map((e) => e.id);
    expect(path).toContain("p");
    expect(path).not.toContain("m-trig");
    expect(path).not.toContain("m-resp");
  });
});

describe("handleRollbackCommand — only the single most-recent qualifying decision", () => {
  it("targets the most-recent auto decision (a second /rollback after a replayed one no-ops)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main"),
      messageEntry("c", "assistant", "tip"),
    ]);
    stageAutoSetCheckpoint(fake, null, "c", "new topic");

    const replay = vi.fn();
    await handleRollbackCommand(deps(replay), msg("/rollback"), trunkFrom(fake));
    expect(replay).toHaveBeenCalledTimes(1);

    // The decision log is cleared → a second /rollback has nothing to target.
    const deliver = vi.fn();
    await handleRollbackCommand({ ...deps(vi.fn()), deliver }, msg("/rollback"), trunkFrom(fake));
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("Nothing to roll back") }),
    );
  });
});

describe("handleRollbackCommand — replay contract", () => {
  it("passes a well-formed DecisionHeader to replay on a successful reversal", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main"),
      messageEntry("c", "assistant", "tip"),
    ]);
    stageAutoSetCheckpoint(fake, null, "c", "trigger");

    const replay = vi.fn();
    await handleRollbackCommand(deps(replay), msg("/rollback"), trunkFrom(fake));

    const header = replay.mock.calls[0]?.[1] as DecisionHeader | undefined;
    expect(header).toBeDefined();
    expect(typeof header?.label).toBe("string");
    expect(typeof header?.note).toBe("string");
    expect(header?.rollbackable).toBe(false);
  });

  it("does NOT replay when Case A's topic collapse fails (degrades with a notice)", async () => {
    const fake = makeSession([
      messageEntry("base", "user", "main"),
      messageEntry("c", "assistant", "tip"),
    ]);
    stageAutoSetCheckpoint(fake, null, "c", "trigger");

    const replay = vi.fn();
    const deliver = vi.fn();
    await handleRollbackCommand(
      {
        side: { complete: vi.fn().mockRejectedValue(new Error("model down")) },
        log: fakeLog,
        deliver,
        replay,
      },
      msg("/rollback"),
      trunkFrom(fake),
    );

    expect(replay).not.toHaveBeenCalled();
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("Couldn't complete") }),
    );
  });
});
