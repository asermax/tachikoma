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

const fakeLog = { info: vi.fn(), error: vi.fn(), debug: vi.fn(), warn: vi.fn() };

interface SetupResult {
  middleware: InboundMiddleware;
  shadowFork: ReturnType<typeof vi.fn>;
  complete: ReturnType<typeof vi.fn>;
  classify: ReturnType<typeof vi.fn>;
  registeredFactory: boolean;
}

const setup = (config: { enabled: boolean } = { enabled: true }): SetupResult => {
  let middleware: InboundMiddleware | null = null;
  let registeredFactory = false;

  const shadowFork = vi.fn();
  const complete = vi.fn().mockResolvedValue("a summary");
  const classify = vi.fn();

  const app = {
    extensionConfig: { enabled: config.enabled },
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
    },
    sessions: { activeTrunkSession: () => null },
    status: vi.fn(),
    log: fakeLog,
  } as unknown as AppContext<{ enabled: boolean }>;

  boundary.setup(app);

  return {
    middleware: middleware as unknown as InboundMiddleware,
    shadowFork,
    complete,
    classify,
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
    ...overrides,
  };
};

const context = (trunk: TrunkInbound | null): InboundContext => ({ trunk });

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
});
