import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import { EventBus } from "../src/events.ts";
import type { PostProcessorContext } from "../src/extensions/api.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import {
  BRANCH_SUMMARY,
  type BranchRecord,
  getBranchRecords,
  TrunkState,
} from "../src/sessions/trunk.ts";

// The coordinator builds a real SideRunner, whose `complete` bottoms out in pi-ai's `completeSimple`.
// Mock it at the module boundary (the pattern `tests/agent/side-run.test.ts` uses) so the live-branch
// summary is deterministic without a real provider call.
const completeSimpleMock = vi.fn();
vi.mock("@earendil-works/pi-ai", () => ({
  completeSimple: (...args: unknown[]) => completeSimpleMock(...args),
}));

const assistantResult = (text: string): AssistantMessage =>
  ({
    role: "assistant",
    content: [{ type: "text", text }],
    api: "anthropic",
    provider: "anthropic",
    model: "model",
    usage: {},
    stopReason: "stop",
    timestamp: 0,
  }) as AssistantMessage;

const createFakeLog = () => {
  const log = { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

const message = (id: string, role: "user" | "assistant", text: string) => ({
  type: "message" as const,
  id,
  parentId: null,
  timestamp: "2026-06-14T10:00:00.000Z",
  message: { role, content: [{ type: "text", text }] },
});

interface CollapseSession {
  session: AgentSession;
  branchCalls: Array<{ branchFromId: string | null; summary: string; details: unknown }>;
}

/**
 * A collapse-aware fake session: an append-only entries list (for `getEntries`/`getEntry`) plus a
 * separate leaf path (for `getBranch`). `branchWithSummary` appends a `branch_summary` entry shaped so
 * `getBranchRecords` recognizes it, and re-seats the leaf path off the abandoned branch — modeling the
 * real tree semantics that make the empty-branch guard idempotent after a collapse.
 */
const makeCollapseSession = (sessionFile: string, initial: SessionEntry[]): CollapseSession => {
  const allEntries: SessionEntry[] = [...initial];
  let leafPath: SessionEntry[] = [...initial];
  let leafId = initial.length > 0 ? (initial[initial.length - 1]?.id ?? null) : null;
  let counter = 100;
  const branchCalls: CollapseSession["branchCalls"] = [];

  const sessionManager = {
    getEntries: () => [...allEntries],
    getEntry: (id: string) => allEntries.find((entry) => entry.id === id),
    getLeafId: () => leafId,
    getBranch: () => [...leafPath],
    getHeader: () => ({ timestamp: "2026-06-14T10:00:00.000Z" }),
    branchWithSummary: vi.fn((branchFromId: string | null, summary: string, details: unknown) => {
      branchCalls.push({ branchFromId, summary, details });
      counter += 1;
      const id = `s-${counter}`;
      const entry = {
        type: "branch_summary",
        id,
        parentId: branchFromId,
        timestamp: "2026-06-14T10:00:00.000Z",
        details,
      } as unknown as SessionEntry;
      allEntries.push(entry);
      const baseIndex =
        branchFromId == null ? -1 : leafPath.findIndex((entry) => entry.id === branchFromId);
      leafPath = [...leafPath.slice(0, baseIndex + 1), entry];
      leafId = id;
      return id;
    }),
    appendCustomEntry: vi.fn((customType: string, data: unknown) => {
      counter += 1;
      const id = `c-${counter}`;
      allEntries.push({
        type: "custom",
        id,
        parentId: leafId,
        timestamp: "2026-06-14T10:00:00.000Z",
        customType,
        data,
      } as unknown as SessionEntry);
      leafId = id;
      return id;
    }),
    branch: (id: string) => {
      leafId = id;
    },
  };

  return {
    session: {
      sessionFile,
      dispose: vi.fn(),
      messages: [],
      sessionManager,
    } as unknown as AgentSession,
    branchCalls,
  };
};

const createAgent = (initial: SessionEntry[]) => {
  const cache = new Map<string, CollapseSession>();
  return {
    agent: {
      open: vi.fn(async (options?: { sessionFile?: string }) => {
        const file = options?.sessionFile ?? "/tmp/new.jsonl";
        if (!cache.has(file)) cache.set(file, makeCollapseSession(file, initial));
        return cache.get(file)?.session as AgentSession;
      }),
      tiers: {
        resolve: () => ({ model: { provider: "anthropic", id: "claude" }, fromPiDefaults: false }),
      },
      apiKeyFor: vi.fn(async () => undefined),
    } as unknown as AgentManager,
    getSession: (file: string) => cache.get(file),
  };
};

const makeCoordinator = (
  db: AppDatabase,
  agent: AgentManager,
  regs: Registrations = createRegistrations(),
  now: () => Date = () => new Date("2026-06-15T10:00:00Z"),
) => {
  const log = createFakeLog();
  const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  const events = new EventBus(log);
  const coordinator = new Coordinator(trunkState, agent, regs, events, log, "UTC", now);
  return { coordinator, trunkState, log };
};

let db: AppDatabase;
let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-coordinator-close-collapse-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
  completeSimpleMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator trunk close — live-branch collapse", () => {
  it("collapses the live branch before extraction so the pipeline sees it", async () => {
    completeSimpleMock.mockResolvedValue(assistantResult("the day's final topic summarized"));

    // A capturing processor records the branch-records snapshot the extraction phase receives.
    let captured: BranchRecord[] = [];
    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "capture",
      phase: "main",
      process: async (ctx: PostProcessorContext) => {
        captured = ctx.trunk?.branchRecords ?? [];
      },
    });

    const initial = [
      message("u1", "user", "one last thing"),
      message("a1", "assistant", "sure thing"),
    ];
    const { agent, getSession } = createAgent(initial);
    const { coordinator, trunkState } = makeCoordinator(db, agent, regs);

    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");
    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();

    // The live branch was collapsed once as a topic branch, via the LLM summary path.
    const session = getSession(file);
    expect(session?.branchCalls).toHaveLength(1);
    expect(session?.branchCalls[0]?.details).toMatchObject({
      customType: BRANCH_SUMMARY,
      branchId: "topic-1",
      kind: "topic",
      originalLeafId: "a1",
      baseId: null,
      reason: "trunk close",
    });

    // ...and the just-collapsed branch is visible to the extraction phase and to getBranchRecords.
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ branchId: "topic-1", originalLeafId: "a1" });
    expect(getBranchRecords(session?.session as AgentSession)).toHaveLength(1);

    // A clean close retires the trunk.
    expect(trunkState.listUnclosed()).toEqual([]);
  });

  it("skips the collapse when the live branch has no assistant turn", async () => {
    completeSimpleMock.mockResolvedValue(assistantResult("summary"));

    let ran = false;
    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "capture",
      phase: "main",
      process: async () => {
        ran = true;
      },
    });

    // Only a user turn — no assistant reply to collapse.
    const initial = [message("u1", "user", "hello?")];
    const { agent, getSession } = createAgent(initial);
    const { coordinator, trunkState } = makeCoordinator(db, agent, regs);

    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");
    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();

    expect(getSession(file)?.branchCalls).toHaveLength(0);
    expect(completeSimpleMock).not.toHaveBeenCalled();
    // Post-processing still runs (the empty-branch guard is a success, not a failure).
    expect(ran).toBe(true);
    expect(trunkState.listUnclosed()).toEqual([]);
  });

  it("aborts the close (trunk not retired) when the collapse fails", async () => {
    completeSimpleMock.mockRejectedValue(new Error("model down"));

    const initial = [
      message("u1", "user", "one last thing"),
      message("a1", "assistant", "sure thing"),
    ];
    const { agent, getSession } = createAgent(initial);
    const { coordinator, trunkState } = makeCoordinator(db, agent);

    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");
    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();

    // The summary threw before any branch_summary was written…
    expect(getSession(file)?.branchCalls).toHaveLength(0);
    // …so the trunk stays unclosed for the next recovery to retry rather than retiring with content lost.
    expect(trunkState.listUnclosed()).toEqual([file]);
  });

  it("is idempotent — a second close does not re-collapse", async () => {
    completeSimpleMock.mockResolvedValue(assistantResult("summary"));

    const initial = [
      message("u1", "user", "one last thing"),
      message("a1", "assistant", "sure thing"),
    ];
    const { agent, getSession } = createAgent(initial);
    const { coordinator, trunkState } = makeCoordinator(db, agent);

    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");
    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();
    expect(getSession(file)?.branchCalls).toHaveLength(1);

    // Re-close the SAME session (recovery re-running after a partial failure): the leaf was re-seated
    // onto the summary, so the empty-branch guard skips a second collapse.
    trunkState.addUnclosed(file);
    await coordinator.recoverStaleTrunks();

    expect(getSession(file)?.branchCalls).toHaveLength(1);
  });
});
