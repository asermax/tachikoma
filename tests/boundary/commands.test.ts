import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";

import { getBranchEntries } from "../../src/agent/session-tree.ts";
import { textMessage } from "../../src/domain/message.ts";
import type { TrunkInbound } from "../../src/extensions/api.ts";
import {
  handleBackCommand,
  handleCheckpointCommand,
} from "../../src/extensions/boundary/commands.ts";
import { TANGENT_FOCUS_CUSTOM_TYPE } from "../../src/extensions/boundary/focus.ts";
import type { Logger } from "../../src/log.ts";
import {
  getAllBranchRecords,
  getBranchRecords,
  readBoomerangState,
  setCheckpoint,
} from "../../src/sessions/trunk.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

const messageEntry = (id: string, role: "user" | "assistant", text: string): SessionEntry =>
  ({
    type: "message",
    id,
    parentId: null,
    timestamp: "2026-06-15T00:00:00Z",
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
  appendMessage: (entry: SessionEntry) => void;
}

/**
 * A faithful fake session: `appendCustomEntry` and `branchWithSummary` advance/re-seat the leaf exactly
 * as real pi does (`_appendEntry` sets `this.leafId`), and `getBranch` walks `parentId` from the leaf —
 * so after a tangent collapse the tangent turns go off-path. This is what makes the empty-tangent guard
 * and the main-line-resumes checks meaningful.
 */
const makeSession = (initial: SessionEntry[] = []): FakeSession => {
  const byId = new Map<string, SessionEntry>();
  let leafId: string | null = null;
  let counter = 0;
  const branchCalls: BranchCall[] = [];

  for (const entry of initial) byId.set(entry.id, entry);
  leafId = initial.at(-1)?.id ?? null;

  const nextId = (prefix: string): string => {
    counter += 1;
    return `${prefix}-${counter}`;
  };

  const appendAt = (entry: SessionEntry, parentId: string | null): void => {
    (entry as { parentId: string | null }).parentId = parentId;
    byId.set(entry.id, entry);
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
    getEntries: () => [...byId.values()],
    getEntry: (id: string) => byId.get(id),
    getLeafId: () => leafId,
    getBranch,
    branchWithSummary: vi.fn((branchFromId: string | null, summary: string, details: unknown) => {
      const id = nextId("sum");
      branchCalls.push({ branchFromId, summary, details, returnedId: id });
      appendAt(
        {
          type: "branch_summary",
          id,
          parentId: null,
          timestamp: "2026-06-15T00:00:00Z",
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
          timestamp: "2026-06-15T00:00:00Z",
          customType,
          data,
        } as unknown as SessionEntry,
        leafId,
      );
      return id;
    }),
    appendCustomMessageEntry: vi.fn(
      (customType: string, content: string, display: boolean, details?: unknown) => {
        const id = nextId("cm");
        appendAt(
          {
            type: "custom_message",
            id,
            parentId: null,
            timestamp: "2026-06-15T00:00:00Z",
            customType,
            content,
            display,
            details,
          } as unknown as SessionEntry,
          leafId,
        );
        return id;
      },
    ),
  };

  return {
    session: { sessionManager } as unknown as AgentSession,
    branchCalls,
    appendMessage: (entry: SessionEntry) => appendAt(entry, leafId),
  };
};

const msg = (text: string) => textMessage("test", text);

const side = { complete: vi.fn().mockResolvedValue("a tangent summary") };

const deliver = (): ReturnType<typeof vi.fn> => vi.fn();

const trunkFrom = (fake: FakeSession, overrides: Partial<TrunkInbound> = {}): TrunkInbound => {
  const checkpointId = readBoomerangState(fake.session)?.checkpointId ?? null;
  return {
    session: fake.session,
    sessionFile: "/tmp/trunk.jsonl",
    currentBaseId: null,
    branchRecords: [],
    liveBranchId: "topic-1",
    hasAssistantTurnSinceBase: true,
    checkpointId,
    lastAutoDecision: null,
    ...overrides,
  };
};

describe("handleCheckpointCommand (/checkpoint)", () => {
  it("sets a checkpoint at the current main-line tip and acks with the label (R1)", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const deliverFn = deliver();
    const message = msg("/checkpoint");

    const handled = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    expect(handled).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
    // The tangent-focus instruction is injected at checkpoint-set time (issue-411), as a hidden
    // custom_message that rides the tangent. Its type is custom_message (not message), so it does not
    // count as a tangent turn (empty-tangent guard) nor appear in the tangent summary.
    expect(fake.session.sessionManager.appendCustomMessageEntry).toHaveBeenCalledWith(
      TANGENT_FOCUS_CUSTOM_TYPE,
      expect.any(String),
      false,
      undefined,
    );
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("📌 Checkpoint set"),
        reaction: "💔",
        immediate: true,
      }),
    );
  });

  it("is idempotent at the tip: a second /checkpoint with no tangent is a no-op + notice (R11)", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    // First /checkpoint sets checkpointId = m2 (the boomerang append advances the leaf past m2).
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");

    const deliverFn = deliver();
    const handled = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      msg("/checkpoint"),
      trunkFrom(fake),
    );

    expect(handled).toBe("acked");
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("already at this tip") }),
    );
    // The checkpoint stays at the message tip (m2), not re-seated to the boomerang entry.
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
  });

  it("overrides a prior checkpoint once a tangent has moved the tip (R2)", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    // A tangent exchange moves the tip forward.
    fake.appendMessage(messageEntry("t1", "user", "side question"));
    fake.appendMessage(messageEntry("t2", "assistant", "side answer"));

    const deliverFn = deliver();
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      msg("/checkpoint"),
      trunkFrom(fake),
    );

    expect(readBoomerangState(fake.session)?.checkpointId).toBe("t2");
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("📌 Checkpoint set"),
        reaction: "💔",
      }),
    );
  });

  it("returns unhandled for a non-/checkpoint message", () => {
    const fake = makeSession([messageEntry("m1", "user", "hi")]);
    const deliverFn = deliver();
    const message = msg("hello there");

    expect(
      handleCheckpointCommand({ side, log: fakeLog, deliver: deliverFn }, message, trunkFrom(fake)),
    ).toBe("unhandled");
    expect(message.metadata.handled).toBeUndefined();
    expect(deliverFn).not.toHaveBeenCalled();
  });

  it("recognizes /checkpoint via the stamped command token when a reply quote lards the text", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const deliverFn = deliver();
    // Reply form: the channel prepended a quote but stamped metadata.command from the raw text — an
    // exact text match would miss this and let the command fall through to the classifier.
    const message = msg("Replied to:\n> earlier\n\n/checkpoint");
    message.metadata.command = "checkpoint";

    expect(
      handleCheckpointCommand({ side, log: fakeLog, deliver: deliverFn }, message, trunkFrom(fake)),
    ).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
  });

  it("sets the checkpoint and streams trailing text as the tangent's first turn", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const deliverFn = deliver();
    const message = msg("/checkpoint let me look into this quickly");
    message.metadata.command = "checkpoint";

    const outcome = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    // Checkpoint is set and the trailing text is stripped onto the message to stream as the first turn.
    expect(outcome).toBe("continue");
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
    expect(message.text).toBe("let me look into this quickly");
    expect(message.metadata.handled).toBeUndefined();
    expect(fake.session.sessionManager.appendCustomMessageEntry).toHaveBeenCalledWith(
      TANGENT_FOCUS_CUSTOM_TYPE,
      expect.any(String),
      false,
      undefined,
    );
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("📌 Checkpoint set"),
        reaction: "💔",
      }),
    );
  });

  it("streams trailing text even when the checkpoint is already at the tip (idempotent + text)", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    // Park the checkpoint at m2 first.
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");

    const deliverFn = deliver();
    const message = msg("/checkpoint one more thing");
    message.metadata.command = "checkpoint";

    const outcome = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    // Idempotent ack, but the checkpoint is in effect so the trailing text still starts the tangent.
    expect(outcome).toBe("continue");
    expect(message.text).toBe("one more thing");
    expect(message.metadata.handled).toBeUndefined();
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("already at this tip") }),
    );
    // The checkpoint stays at m2 (no re-set, no boomerang leaf advance).
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
  });

  it("extracts only the trailing text when a reply quote lards a /checkpoint <text>", () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const message = msg("Replied to:\n> earlier /checkpoint noise\n\n/checkpoint the real tangent");
    message.metadata.command = "checkpoint";

    const outcome = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      message,
      trunkFrom(fake),
    );

    // The command token is the user's input (appended last); the quote's "/checkpoint noise" is ignored.
    expect(outcome).toBe("continue");
    expect(message.text).toBe("the real tangent");
  });

  it("acks and does not stream trailing text when there is no conversation (guard failure)", () => {
    const fake = makeSession();
    const deliverFn = deliver();
    const message = msg("/checkpoint hello");
    message.metadata.command = "checkpoint";

    const outcome = handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    expect(outcome).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(message.text).toBe("/checkpoint hello"); // not stripped — no turn streams
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("No conversation") }),
    );
  });
});

describe("handleBackCommand (/back)", () => {
  it("no-ops with a notice when there is no active checkpoint (R11)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const sideNoCall = { complete: vi.fn() };
    const deliverFn = deliver();
    const message = msg("/back");

    const handled = await handleBackCommand(
      { side: sideNoCall, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    expect(handled).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("No checkpoint") }),
    );
    expect(fake.branchCalls).toHaveLength(0);
    expect(sideNoCall.complete).not.toHaveBeenCalled();
  });

  it("no-ops with a notice when the checkpoint has no tangent (empty-tangent guard, R11/KD2)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    // checkpointId = m2; the only thing after it is the boomerang entry setCheckpoint appended — no turn.

    const sideNoCall = { complete: vi.fn().mockResolvedValue("summary") };
    const deliverFn = deliver();
    await handleBackCommand(
      { side: sideNoCall, log: fakeLog, deliver: deliverFn },
      msg("/back"),
      trunkFrom(fake),
    );

    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("No tangent") }),
    );
    expect(fake.branchCalls).toHaveLength(0); // the collapse primitive is NOT called
    expect(sideNoCall.complete).not.toHaveBeenCalled();
    // The checkpoint is left in place.
    expect(readBoomerangState(fake.session)?.checkpointId).toBe("m2");
  });

  it("summarizes a one-turn tangent back to the checkpoint, parks it, and clears the checkpoint (R3/R5)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "main"),
      messageEntry("m2", "assistant", "line"),
    ]);
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    // One tangent exchange after the checkpoint.
    fake.appendMessage(messageEntry("t1", "user", "quick side question"));
    fake.appendMessage(messageEntry("t2", "assistant", "side answer"));

    const sideOne = { complete: vi.fn().mockResolvedValue("tangent summary text") };
    const deliverFn = deliver();
    const handled = await handleBackCommand(
      { side: sideOne, log: fakeLog, deliver: deliverFn },
      msg("/back"),
      trunkFrom(fake),
    );

    expect(handled).toBe("acked");
    // collapseTangent rooted at the checkpoint (m2), marked kind tangent.
    expect(fake.branchCalls).toHaveLength(1);
    expect(fake.branchCalls[0]?.branchFromId).toBe("m2");
    expect(fake.branchCalls[0]?.details).toMatchObject({
      kind: "tangent",
      branchId: "tangent-1",
      baseId: "m2",
    });
    // The summary covers the tangent turns (t1/t2), not the main line.
    expect(sideOne.complete).toHaveBeenCalledWith(
      expect.objectContaining({ user: expect.stringContaining("quick side question") }),
    );
    // The tangent is parked away — excluded from topic branch records, present in the unfiltered set.
    expect(getBranchRecords(fake.session)).toEqual([]);
    expect(getAllBranchRecords(fake.session).map((r) => r.summaryEntryId)).toEqual([
      fake.branchCalls[0]?.returnedId,
    ]);
    // Checkpoint cleared.
    expect(readBoomerangState(fake.session)?.checkpointId).toBeNull();
    // The main line resumes at the checkpoint: m2 and the tangent summary are on the leaf path, while
    // the tangent turns are folded off-path. (clearCheckpoint then appends a boomerang entry, so the
    // leaf itself is that marker — the summary sits one step back on the main line.)
    const path = getBranchEntries(fake.session).map((e) => e.id);
    expect(path).toContain("m2");
    expect(path).toContain(fake.branchCalls[0]?.returnedId);
    expect(path).not.toContain("t1");
    expect(path).not.toContain("t2");
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("↩️ Summarized to checkpoint"),
        reaction: "❤",
      }),
    );
  });

  it("summarizes a multi-turn tangent through the same single path (R4 — one-turn == multi-turn)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "main"),
      messageEntry("m2", "assistant", "line"),
    ]);
    setCheckpoint(fake.session, "m2");
    // Several tangent exchanges — same mechanism as the one-turn case.
    fake.appendMessage(messageEntry("t1", "user", "q1"));
    fake.appendMessage(messageEntry("t2", "assistant", "a1"));
    fake.appendMessage(messageEntry("t3", "user", "q2"));
    fake.appendMessage(messageEntry("t4", "assistant", "a2"));

    const sideMulti = { complete: vi.fn().mockResolvedValue("multi-turn tangent summary") };
    await handleBackCommand(
      { side: sideMulti, log: fakeLog, deliver: deliver() },
      msg("/back"),
      trunkFrom(fake),
    );

    // One collapse rooted at the checkpoint, same as the one-turn case.
    expect(fake.branchCalls).toHaveLength(1);
    expect(fake.branchCalls[0]?.branchFromId).toBe("m2");
    expect(fake.branchCalls[0]?.details).toMatchObject({ kind: "tangent", branchId: "tangent-1" });
    expect(getBranchRecords(fake.session)).toEqual([]);
    expect(readBoomerangState(fake.session)?.checkpointId).toBeNull();
  });

  it("returns unhandled for a non-/back message", async () => {
    const fake = makeSession([messageEntry("m1", "user", "hi")]);
    const deliverFn = deliver();
    const message = msg("hello there");

    expect(
      await handleBackCommand({ side, log: fakeLog, deliver: deliverFn }, message, trunkFrom(fake)),
    ).toBe("unhandled");
    expect(message.metadata.handled).toBeUndefined();
    expect(deliverFn).not.toHaveBeenCalled();
  });

  it("recognizes /back via the stamped command token when a reply quote lards the text", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "main"),
      messageEntry("m2", "assistant", "line"),
    ]);
    setCheckpoint(fake.session, "m2");
    fake.appendMessage(messageEntry("t1", "user", "quick side question"));
    fake.appendMessage(messageEntry("t2", "assistant", "side answer"));

    const deliverFn = deliver();
    const message = msg("Replied to:\n> earlier\n\n/back");
    message.metadata.command = "back";

    const handled = await handleBackCommand(
      { side, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    expect(handled).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(fake.branchCalls).toHaveLength(1);
  });

  it("summarizes the tangent and streams trailing text as the resumed main line's first turn", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "main"),
      messageEntry("m2", "assistant", "line"),
    ]);
    handleCheckpointCommand(
      { side, log: fakeLog, deliver: deliver() },
      msg("/checkpoint"),
      trunkFrom(fake),
    );
    fake.appendMessage(messageEntry("t1", "user", "quick side question"));
    fake.appendMessage(messageEntry("t2", "assistant", "side answer"));

    const deliverFn = deliver();
    const message = msg("/back here's what I found");
    message.metadata.command = "back";

    const outcome = await handleBackCommand(
      {
        side: { complete: vi.fn().mockResolvedValue("tangent summary") },
        log: fakeLog,
        deliver: deliverFn,
      },
      message,
      trunkFrom(fake),
    );

    // Tangent folded + cleared, and the trailing text is stripped onto the message to stream.
    expect(outcome).toBe("continue");
    expect(fake.branchCalls).toHaveLength(1);
    expect(readBoomerangState(fake.session)?.checkpointId).toBeNull();
    expect(message.text).toBe("here's what I found");
    expect(message.metadata.handled).toBeUndefined();
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("↩️ Summarized to checkpoint"),
        reaction: "❤",
      }),
    );
  });

  it("acks and does not stream trailing text when there is no checkpoint (guard failure)", async () => {
    const fake = makeSession([
      messageEntry("m1", "user", "hi"),
      messageEntry("m2", "assistant", "hello"),
    ]);
    const deliverFn = deliver();
    const message = msg("/back here's what I found");
    message.metadata.command = "back";

    const outcome = await handleBackCommand(
      { side: { complete: vi.fn() }, log: fakeLog, deliver: deliverFn },
      message,
      trunkFrom(fake),
    );

    expect(outcome).toBe("acked");
    expect(message.metadata.handled).toBe(true);
    expect(message.text).toBe("/back here's what I found"); // not stripped — no turn streams
    expect(fake.branchCalls).toHaveLength(0);
    expect(deliverFn).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("No checkpoint") }),
    );
  });
});
