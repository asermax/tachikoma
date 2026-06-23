import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { describe, expect, it, vi } from "vitest";
import { textMessage } from "../../src/domain/message.ts";
import type {
  AppContext,
  InboundContext,
  InboundMiddleware,
  TrunkInbound,
} from "../../src/extensions/api.ts";
import boundary from "../../src/extensions/boundary/index.ts";
import { BOOMERANG_STATE } from "../../src/sessions/trunk.ts";

const fakeLog = { info: vi.fn(), error: vi.fn(), debug: vi.fn(), warn: vi.fn() };

interface SetupConfig {
  enabled?: boolean;
  autoSetCheckpoint?: boolean;
  autoSummarizeToCheckpoint?: boolean;
}

interface SetupResult {
  middleware: InboundMiddleware;
  shadowFork: ReturnType<typeof vi.fn>;
  complete: ReturnType<typeof vi.fn>;
  classify: ReturnType<typeof vi.fn>;
  deliver: ReturnType<typeof vi.fn>;
  status: ReturnType<typeof vi.fn>;
  registeredFactory: boolean;
}

const setup = (config: SetupConfig = {}): SetupResult => {
  const { enabled = true, autoSetCheckpoint = true, autoSummarizeToCheckpoint = true } = config;
  let middleware: InboundMiddleware | null = null;
  let registeredFactory = false;

  const shadowFork = vi.fn();
  const complete = vi.fn().mockResolvedValue("a summary");
  const classify = vi.fn();
  const deliver = vi.fn();
  const status = vi.fn();

  const app = {
    extensionConfig: { enabled, autoSetCheckpoint, autoSummarizeToCheckpoint },
    inbound: {
      use: (registered: InboundMiddleware) => {
        middleware = registered;
      },
    },
    agent: {
      use: () => {
        registeredFactory = true;
      },
      side: { complete, classify },
      shadowFork,
      branchFile: vi.fn(),
    },
    channels: { deliver },
    sessions: { activeTrunkSession: () => null },
    status,
    log: fakeLog,
  } as unknown as AppContext<{
    enabled: boolean;
    autoSetCheckpoint: boolean;
    autoSummarizeToCheckpoint: boolean;
  }>;

  boundary.setup(app);

  return {
    middleware: middleware as unknown as InboundMiddleware,
    shadowFork,
    complete,
    classify,
    deliver,
    status,
    registeredFactory,
  };
};

// A live trunk session whose sessionManager records the collapse (branchWithSummary).
const makeTrunk = (overrides: Partial<TrunkInbound> = {}): TrunkInbound => {
  const branchWithSummary = vi.fn().mockReturnValue("summary-id");
  const session = {
    systemPrompt: "you are helpful",
    sessionManager: {
      branchWithSummary,
      getBranch: () => [
        {
          id: "leaf",
          type: "message",
          message: { role: "assistant", content: [{ type: "text", text: "done" }] },
        },
      ],
      getLeafId: () => "leaf",
      getEntries: () => [],
      appendCustomEntry: vi.fn().mockReturnValue("c-1"),
      appendCustomMessageEntry: vi.fn().mockReturnValue("c-2"),
      getEntry: (id: string) =>
        id === "summary-topic-1"
          ? { id, type: "branch_summary", summary: "earlier topic summary" }
          : undefined,
    },
  } as unknown as AgentSession;

  const branchRecords = overrides.branchRecords ?? [];

  return {
    session,
    sessionFile: "/tmp/trunk.jsonl",
    currentBaseId: null,
    branchRecords,
    liveBranchId: `topic-${branchRecords.length + 1}`,
    hasAssistantTurnSinceBase: true,
    checkpointId: null,
    lastAutoDecision: null,
    ...overrides,
  };
};

const context = (trunk: TrunkInbound | null): InboundContext => ({ trunk });

const messageEntry = (id: string, role: "user" | "assistant", text: string) => ({
  type: "message" as const,
  id,
  parentId: null,
  timestamp: "2026-06-15T00:00:00Z",
  message: { role, content: [{ type: "text", text }] },
});

/**
 * A trunk session whose `getBranch` returns a fixed leaf path, so `checkpointHasTangent` is
 * deterministic: pass a branch that starts at the checkpoint id and is followed (or not) by message
 * turns. Used for the auto summarize-to-checkpoint path, which runs the real `checkpointHasTangent` +
 * `summarizeCurrentTangent` against the session.
 */
const trunkWithBranch = (
  branch: Array<ReturnType<typeof messageEntry>>,
  overrides: Partial<TrunkInbound> = {},
): TrunkInbound => {
  const session = {
    systemPrompt: "you are helpful",
    sessionManager: {
      branchWithSummary: vi.fn().mockReturnValue("tangent-summary-id"),
      appendCustomEntry: vi.fn().mockReturnValue("c-1"),
      appendCustomMessageEntry: vi.fn().mockReturnValue("c-2"),
      getBranch: () => branch,
      getEntries: () => [],
      getEntry: () => undefined,
      getLeafId: () => "leaf",
    },
  } as unknown as AgentSession;

  return makeTrunk({ session, ...overrides });
};

/** Fork mock whose `prompt` resolves with the given classifier JSON. */
const forkDeciding = (decision: string) => ({
  prompt: vi.fn().mockResolvedValue(`{"decision":"${decision}","reason":"r"}`),
  dispose: vi.fn().mockResolvedValue(undefined),
});

describe("boundary middleware", () => {
  it("registers the ask_branch tool factory", () => {
    expect(setup().registeredFactory).toBe(true);
  });

  it("skips detection for system-origin (boundary:skip) messages", async () => {
    const { middleware, shadowFork } = setup();
    const trunk = makeTrunk();
    const next = vi.fn();
    const message = textMessage("test", "scheduled task");
    message.metadata.boundary = "skip";

    await middleware(message, context(trunk), next);

    expect(shadowFork).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("skips detection when there is no trunk", async () => {
    const { middleware, shadowFork } = setup();
    const next = vi.fn();

    await middleware(textMessage("test", "hi"), context(null), next);

    expect(shadowFork).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("forces a collapse on /new when the branch has an assistant turn", async () => {
    const { middleware, shadowFork } = setup();
    const trunk = makeTrunk({ hasAssistantTurnSinceBase: true });
    const next = vi.fn();
    const message = textMessage("test", "start over");
    message.metadata.forceNew = true;

    await middleware(message, context(trunk), next);

    // Forced shift: no classifier, but the branch collapsed.
    expect(shadowFork).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("skips the forced collapse on /new for an empty branch", async () => {
    const { middleware } = setup();
    const trunk = makeTrunk({ hasAssistantTurnSinceBase: false });
    const next = vi.fn();
    const message = textMessage("test", "start over");
    message.metadata.forceNew = true;

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("collapses the current branch on a classified shift", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue({
      prompt: vi.fn().mockResolvedValue('{"decision":"shift","reason":"new topic"}'),
      dispose: vi.fn().mockResolvedValue(undefined),
    });

    const trunk = makeTrunk();
    const next = vi.fn();

    await middleware(textMessage("test", "let's talk about taxes"), context(trunk), next);

    expect(shadowFork).toHaveBeenCalledTimes(1);
    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("does not collapse on a classified continue", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue({
      prompt: vi.fn().mockResolvedValue('{"decision":"continue","reason":"follow-up"}'),
      dispose: vi.fn().mockResolvedValue(undefined),
    });

    const trunk = makeTrunk();
    const next = vi.fn();

    await middleware(textMessage("test", "and then?"), context(trunk), next);

    expect(shadowFork).toHaveBeenCalledTimes(1);
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("skips classification entirely when detection is disabled", async () => {
    const { middleware, shadowFork } = setup({ enabled: false });
    const trunk = makeTrunk();
    const next = vi.fn();

    await middleware(textMessage("test", "anything"), context(trunk), next);

    expect(shadowFork).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("still honors /new when detection is disabled", async () => {
    const { middleware } = setup({ enabled: false });
    const trunk = makeTrunk();
    const next = vi.fn();
    const message = textMessage("test", "start over");
    message.metadata.forceNew = true;

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
  });

  // A collapsed earlier branch (topic-1); with one record the live branch is topic-2.
  const earlierBranchTrunk = (overrides: Partial<TrunkInbound> = {}): TrunkInbound =>
    makeTrunk({
      branchRecords: [
        {
          branchId: "topic-1",
          originalLeafId: "leaf-1",
          baseId: null,
          summaryEntryId: "summary-topic-1",
          lastExchange: "user: earlier\nassistant: earlier reply",
        },
      ],
      ...overrides,
    });

  it("forces a shift and injects context for a reply/reaction referencing an earlier branch", async () => {
    const { middleware, shadowFork } = setup();
    const trunk = earlierBranchTrunk();
    const next = vi.fn();
    const message = textMessage("test", "back to the earlier thing");
    message.metadata.forcedBranchId = "topic-1";
    message.metadata.forcedTreeEntryId = "entry-x";

    await middleware(message, context(trunk), next);

    // Forced: no classifier, branch collapsed, and the referenced branch's context injected.
    expect(shadowFork).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(trunk.session.sessionManager.appendCustomMessageEntry).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("appends without collapse when the forced reference is the live branch", async () => {
    const { middleware, shadowFork } = setup();
    // The live branch is topic-2 (one collapsed record). A reference to it just appends.
    const trunk = earlierBranchTrunk();
    const next = vi.fn();
    const message = textMessage("test", "more on this");
    message.metadata.forcedBranchId = "topic-2";

    await middleware(message, context(trunk), next);

    expect(shadowFork).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.appendCustomMessageEntry).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("falls through to the classifier for an unrecorded forced reference", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue({
      prompt: vi.fn().mockResolvedValue('{"decision":"continue","reason":"follow-up"}'),
      dispose: vi.fn().mockResolvedValue(undefined),
    });
    const trunk = earlierBranchTrunk();
    const next = vi.fn();
    const message = textMessage("test", "huh");
    // A branch id that resolves to no record (e.g. a stale routing row) → normal detection.
    message.metadata.forcedBranchId = "topic-9";

    await middleware(message, context(trunk), next);

    expect(shadowFork).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("skips the empty-branch collapse on a forced shift but still injects context", async () => {
    const { middleware } = setup();
    const trunk = earlierBranchTrunk({ hasAssistantTurnSinceBase: false });
    const next = vi.fn();
    const message = textMessage("test", "earlier please");
    message.metadata.forcedBranchId = "topic-1";

    await middleware(message, context(trunk), next);

    // Empty current branch: no empty summary emitted, but the referenced branch's context is injected.
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.appendCustomMessageEntry).toHaveBeenCalledTimes(1);
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("handles /checkpoint before the classifier: acks, marks handled, never classifies", async () => {
    const { middleware, shadowFork, deliver } = setup();
    const trunk = makeTrunk();
    const next = vi.fn();
    const message = textMessage("test", "/checkpoint");

    await middleware(message, context(trunk), next);

    expect(message.metadata.handled).toBe(true);
    expect(next).not.toHaveBeenCalled();
    expect(shadowFork).not.toHaveBeenCalled();
    // A checkpoint was written and the ack carries the label.
    expect(trunk.session.sessionManager.appendCustomEntry).toHaveBeenCalled();
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("📌 Checkpoint set"),
        immediate: true,
      }),
    );
  });

  it("handles /back before the classifier even with no checkpoint (no-op notice)", async () => {
    const { middleware, shadowFork, deliver } = setup();
    const trunk = makeTrunk(); // no active checkpoint
    const next = vi.fn();
    const message = textMessage("test", "/back");

    await middleware(message, context(trunk), next);

    expect(message.metadata.handled).toBe(true);
    expect(next).not.toHaveBeenCalled();
    expect(shadowFork).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("No checkpoint"), immediate: true }),
    );
  });

  it("handles a reply-quoted /checkpoint (command token stamped) before the classifier", async () => {
    const { middleware, shadowFork, deliver, status } = setup();
    const trunk = makeTrunk();
    const next = vi.fn();
    // The channel prepended a reply quote but stamped metadata.command from the raw text — so the
    // command is still recognized and never reaches the classifier (no "Checking conversation topic").
    const message = textMessage("test", "Replied to:\n> earlier\n\n/checkpoint");
    message.metadata.command = "checkpoint";

    await middleware(message, context(trunk), next);

    expect(message.metadata.handled).toBe(true);
    expect(next).not.toHaveBeenCalled();
    expect(shadowFork).not.toHaveBeenCalled();
    expect(status).not.toHaveBeenCalledWith("Checking conversation topic…");
    expect(deliver).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining("📌 Checkpoint set"),
        immediate: true,
      }),
    );
  });

  // ---- DLT-181 Batch 3: automatic set-checkpoint / summarize-to-checkpoint ----

  it("auto set-checkpoint writes the checkpoint + lastAutoDecision + header, and does not collapse", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("set-checkpoint"));
    const trunk = makeTrunk({ checkpointId: null });
    const next = vi.fn();
    const message = textMessage("test", "a quick related side question");

    await middleware(message, context(trunk), next);

    // No topic collapse — the message continues inline as the first tangent turn.
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    // The auto decision was recorded and a checkpoint set (both append boomerang state). Asserted as
    // separate calls: the mock's getEntries=[] means readBoomerangState can't merge them into one
    // snapshot — that merge is a trunk.ts invariant exercised faithfully in collapse.test.ts.
    expect(trunk.session.sessionManager.appendCustomEntry).toHaveBeenCalledWith(
      BOOMERANG_STATE,
      expect.objectContaining({
        lastAutoDecision: { kind: "set-checkpoint", preDecisionLeafId: "leaf" },
      }),
    );
    expect(trunk.session.sessionManager.appendCustomEntry).toHaveBeenCalledWith(
      BOOMERANG_STATE,
      expect.objectContaining({ checkpointId: "leaf" }),
    );
    // The streamed response carries the turn-scoped decision header (rollback target).
    expect(message.metadata.decisionHeader).toMatchObject({
      label: "📌 Checkpoint set",
      rollbackable: true,
    });
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("suppresses auto set-checkpoint when a checkpoint is already active (degrades to continue)", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("set-checkpoint"));
    const trunk = makeTrunk({ checkpointId: "prior-checkpoint" });
    const next = vi.fn();
    const message = textMessage("test", "another tangent turn");

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.appendCustomEntry).not.toHaveBeenCalled();
    expect(message.metadata.decisionHeader).toBeUndefined();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("suppresses auto set-checkpoint when autoSetCheckpoint is false (kill-switch)", async () => {
    const { middleware, shadowFork } = setup({ autoSetCheckpoint: false });
    shadowFork.mockResolvedValue(forkDeciding("set-checkpoint"));
    const trunk = makeTrunk({ checkpointId: null });
    const next = vi.fn();

    await middleware(textMessage("test", "a side question"), context(trunk), next);

    expect(trunk.session.sessionManager.appendCustomEntry).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("auto summarize-to-checkpoint folds the tangent, clears the checkpoint, and sets a header", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("summarize-to-checkpoint"));
    const trunk = trunkWithBranch(
      [messageEntry("checkpoint", "assistant", "base"), messageEntry("t1", "user", "q")],
      { checkpointId: "checkpoint" },
    );
    const next = vi.fn();
    const message = textMessage("test", "anyway, back to the main thing");

    await middleware(message, context(trunk), next);

    // The tangent folded into a summary rooted at the checkpoint (kind tangent, fromHook=true).
    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledWith(
      "checkpoint",
      expect.any(String),
      expect.objectContaining({ kind: "tangent", baseId: "checkpoint" }),
      true,
    );
    // The checkpoint cleared.
    expect(trunk.session.sessionManager.appendCustomEntry).toHaveBeenCalledWith(
      BOOMERANG_STATE,
      expect.objectContaining({ checkpointId: null }),
    );
    // Not a rollback target.
    expect(message.metadata.decisionHeader).toMatchObject({
      label: "↩️ Summarized to checkpoint",
      rollbackable: false,
    });
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("suppresses auto summarize-to-checkpoint when no checkpoint is active", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("summarize-to-checkpoint"));
    const trunk = makeTrunk({ checkpointId: null });
    const next = vi.fn();
    const message = textMessage("test", "back to main");

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(message.metadata.decisionHeader).toBeUndefined();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("suppresses auto summarize-to-checkpoint when the checkpoint has no tangent (empty-tangent guard)", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("summarize-to-checkpoint"));
    // Only the checkpoint on the path — no turn follows, so checkpointHasTangent is false.
    const trunk = trunkWithBranch([messageEntry("checkpoint", "assistant", "base")], {
      checkpointId: "checkpoint",
    });
    const next = vi.fn();
    const message = textMessage("test", "back to main");

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(message.metadata.decisionHeader).toBeUndefined();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("suppresses auto summarize-to-checkpoint when autoSummarizeToCheckpoint is false (kill-switch)", async () => {
    const { middleware, shadowFork } = setup({ autoSummarizeToCheckpoint: false });
    shadowFork.mockResolvedValue(forkDeciding("summarize-to-checkpoint"));
    const trunk = trunkWithBranch(
      [messageEntry("checkpoint", "assistant", "base"), messageEntry("t1", "user", "q")],
      { checkpointId: "checkpoint" },
    );
    const next = vi.fn();

    await middleware(textMessage("test", "back to main"), context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("fails open to continue (no state change) when the classifier fork throws", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockRejectedValue(new Error("classifier down"));
    const trunk = makeTrunk();
    const next = vi.fn();

    await middleware(textMessage("test", "something"), context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.appendCustomEntry).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("records lastAutoDecision {kind:new} on an automatic topic shift and surfaces a rollbackable header", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("shift"));
    const trunk = makeTrunk({ hasAssistantTurnSinceBase: true });
    const next = vi.fn();
    const message = textMessage("test", "let's talk about something else");

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(trunk.session.sessionManager.appendCustomEntry).toHaveBeenCalledWith(
      BOOMERANG_STATE,
      expect.objectContaining({
        lastAutoDecision: { kind: "new", preDecisionLeafId: "leaf" },
      }),
    );
    // The shifted response carries the turn-scoped decision header — set exactly where the rollback
    // target is recorded, so the two coincide and /rollback is signalled as available (R8).
    expect(message.metadata.decisionHeader).toMatchObject({
      label: "🆕 New topic",
      rollbackable: true,
    });
  });

  it("does NOT set a header on an empty-branch auto shift (no collapse, no rollback target)", async () => {
    const { middleware, shadowFork } = setup();
    shadowFork.mockResolvedValue(forkDeciding("shift"));
    const trunk = makeTrunk({ hasAssistantTurnSinceBase: false });
    const next = vi.fn();
    const message = textMessage("test", "let's talk about something else");

    await middleware(message, context(trunk), next);

    // Empty current branch: no summary emitted, so no collapse, no recorded decision, and no header.
    expect(trunk.session.sessionManager.branchWithSummary).not.toHaveBeenCalled();
    expect(message.metadata.decisionHeader).toBeUndefined();
    expect(next).toHaveBeenCalledTimes(1);
  });

  it("does NOT record lastAutoDecision on a manual /new (only automatic decisions are rollback targets)", async () => {
    const { middleware, shadowFork } = setup();
    const trunk = makeTrunk({ hasAssistantTurnSinceBase: true });
    const next = vi.fn();
    const message = textMessage("test", "start over");
    message.metadata.forceNew = true;

    await middleware(message, context(trunk), next);

    expect(trunk.session.sessionManager.branchWithSummary).toHaveBeenCalledTimes(1);
    expect(shadowFork).not.toHaveBeenCalled();
    expect(trunk.session.sessionManager.appendCustomEntry).not.toHaveBeenCalledWith(
      BOOMERANG_STATE,
      expect.objectContaining({ lastAutoDecision: { kind: "new", preDecisionLeafId: "leaf" } }),
    );
    // Manual /new is surfaced via its inline ack, not a streamed header (ack-vs-header split).
    expect(message.metadata.decisionHeader).toBeUndefined();
  });
});
