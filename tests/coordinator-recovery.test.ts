import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
import type { Channel } from "../src/channels/types.ts";
import { Coordinator } from "../src/coordinator.ts";
import { type AppDatabase, createDatabase, runMigrations } from "../src/db/index.ts";
import { KeyValueState } from "../src/db/state.ts";
import { EventBus } from "../src/events.ts";
import { createRegistrations, type Registrations } from "../src/extensions/registrations.ts";
import type { Logger } from "../src/log.ts";
import { TrunkState } from "../src/sessions/trunk.ts";

const createFakeLog = () => {
  const log = { warn: vi.fn(), info: vi.fn(), debug: vi.fn(), error: vi.fn() };
  return Object.assign(log, { child: () => log }) as unknown as Logger;
};

const fakeSession = (sessionFile: string, createdAt?: string) => ({
  sessionFile,
  dispose: vi.fn(),
  messages: [],
  sessionManager: {
    getEntries: () => [],
    getLeafId: () => null,
    getBranch: () => [],
    getHeader: () => (createdAt != null ? { timestamp: createdAt } : null),
  },
});

// An agent that returns a fresh fake session keyed on the opened file. `createdAt` optionally maps a
// file to its session-header timestamp, so recovery's day-derivation can be exercised.
const createAgent = (createdAt?: (file: string) => string | undefined) =>
  ({
    open: vi.fn(async (options?: { sessionFile?: string }) => {
      const file = options?.sessionFile ?? "/tmp/new.jsonl";
      return fakeSession(file, createdAt?.(file));
    }),
  }) as unknown as AgentManager;

const makeCoordinator = (
  db: AppDatabase,
  agent: AgentManager,
  regs: Registrations = createRegistrations(),
  now: () => Date = () => new Date(),
) => {
  const log = createFakeLog();
  const trunkState = new TrunkState(new KeyValueState(db, "trunk"));
  const events = new EventBus(log);
  const coordinator = new Coordinator(trunkState, agent, regs, events, log, "UTC", now);

  return { coordinator, trunkState, log };
};

/** Minimal Channel stub whose `lifecycleStatus` is observable; all other methods are no-ops. */
const stubChannel = (lifecycleStatus: ReturnType<typeof vi.fn>): Channel => ({
  name: "test",
  start: vi.fn(async () => {}),
  respond: vi.fn(async () => {}),
  deliver: vi.fn(async () => {}),
  stop: vi.fn(async () => {}),
  lifecycleStatus,
});

const dayOf = (iso: string) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "UTC" }).format(new Date(iso));

let db: AppDatabase;
let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-coordinator-recovery-"));
  db = createDatabase(join(dir, "test.db"));
  runMigrations(db);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Coordinator.recoverStaleTrunks", () => {
  it("is a clean no-op on the first run ever (no pointer, no files)", async () => {
    const regs = createRegistrations();
    const ran: string[] = [];
    regs.postProcessors.push({
      name: "extract",
      process: async () => void ran.push("extract"),
    });

    const { coordinator } = makeCoordinator(db, createAgent(), regs);

    await expect(coordinator.recoverStaleTrunks()).resolves.toBeUndefined();
    expect(ran).toEqual([]);
  });

  it("lazily closes a stale-day active trunk through the close pipeline", async () => {
    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    const closed: (string | null)[] = [];
    regs.postProcessors.push({
      name: "extract",
      process: async (ctx) => void closed.push(ctx.transcriptPath),
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);

    // An active pointer left on an earlier day.
    trunkState.promoteToActive({
      sessionFile: file,
      day: dayOf("2026-06-14T10:00:00Z"),
      openedAt: "2026-06-14T10:00:00Z",
    });

    await coordinator.recoverStaleTrunks();

    expect(closed).toEqual([file]);
    // The stale trunk is retired from the unclosed index and the pointer cleared.
    expect(trunkState.listUnclosed()).toEqual([]);
    expect(trunkState.getActive()).toBeNull();
  });

  it("closes every stale trunk in the unclosed index (multi-day downtime)", async () => {
    const a = join(dir, "day1.jsonl");
    const b = join(dir, "day2.jsonl");
    await writeFile(a, "");
    await writeFile(b, "");

    const regs = createRegistrations();
    const closed: (string | null)[] = [];
    regs.postProcessors.push({
      name: "extract",
      process: async (ctx) => void closed.push(ctx.transcriptPath),
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);

    // Two prior-day trunks left unclosed (no active pointer).
    trunkState.addUnclosed(a);
    trunkState.addUnclosed(b);

    await coordinator.recoverStaleTrunks();

    expect(closed.sort()).toEqual([a, b].sort());
    expect(trunkState.listUnclosed()).toEqual([]);
  });

  it("retires an unclosed entry whose file is gone without erroring", async () => {
    const missing = join(dir, "vanished.jsonl");

    const { coordinator, trunkState } = makeCoordinator(db, createAgent());
    trunkState.addUnclosed(missing);

    await expect(coordinator.recoverStaleTrunks()).resolves.toBeUndefined();
    expect(trunkState.listUnclosed()).toEqual([]);
  });

  it("skips a same-day active pointer (nothing stale to recover)", async () => {
    const file = join(dir, "today.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    const closed: string[] = [];
    regs.postProcessors.push({
      name: "extract",
      process: async () => void closed.push("ran"),
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);

    trunkState.promoteToActive({
      sessionFile: file,
      day: dayOf("2026-06-15T10:00:00Z"),
      openedAt: "2026-06-15T08:00:00Z",
    });

    await coordinator.recoverStaleTrunks();

    expect(closed).toEqual([]);
    // The same-day pointer is left intact for the coordinator to reopen.
    expect(trunkState.getActive()?.sessionFile).toBe(file);
  });

  it("runs registered post-processors only over branches lacking a marker (idempotent close)", async () => {
    // Recovery re-runs the close pipeline; per-branch idempotency is enforced by the B6 processors
    // via markers. B4 only guarantees the close pipeline is invoked once per stale trunk and the
    // trunk is retired afterwards — assert the pipeline ran and the trunk was retired.
    const file = join(dir, "interrupted.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    let runs = 0;
    regs.postProcessors.push({
      name: "extract",
      process: async () => {
        runs += 1;
      },
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);

    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();
    expect(runs).toBe(1);
    expect(trunkState.listUnclosed()).toEqual([]);

    // A second recovery pass finds nothing unclosed → does not re-run.
    await coordinator.recoverStaleTrunks();
    expect(runs).toBe(1);
  });

  it("leaves a trunk unclosed when a post-processor fails, so the next recovery retries it", async () => {
    const file = join(dir, "half-extracted.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    let attempts = 0;
    regs.postProcessors.push({
      name: "extract",
      // Fail the first close (a partial extraction), succeed on the retry.
      process: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("partial extraction");
      },
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);

    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();
    // The failed close did NOT retire the trunk — it stays in the index for another pass.
    expect(attempts).toBe(1);
    expect(trunkState.listUnclosed()).toEqual([file]);

    await coordinator.recoverStaleTrunks();
    // The retry succeeds and the trunk is finally retired.
    expect(attempts).toBe(2);
    expect(trunkState.listUnclosed()).toEqual([]);
  });

  it("dates a recovered unclosed trunk by its session creation day, not the day recovery runs", async () => {
    const file = join(dir, "five-days-stale.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    const days: (string | null)[] = [];
    regs.postProcessors.push({
      name: "extract",
      process: async (ctx) => void days.push(ctx.trunk?.day ?? null),
    });

    // Recovery runs on the 18th; the trunk's session was created on the 13th.
    const now = () => new Date("2026-06-18T10:00:00Z");
    const agent = createAgent(() => "2026-06-13T09:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, agent, regs, now);

    trunkState.addUnclosed(file);

    await coordinator.recoverStaleTrunks();

    // The day handed to the close pipeline is the conversation's real day, so its memories file there.
    expect(days).toEqual(["2026-06-13"]);
    expect(trunkState.listUnclosed()).toEqual([]);
  });
});

describe("Coordinator.recoverStaleTrunks lifecycle visibility", () => {
  it("buffers lifecycle status before the channel attaches and flushes it in order", async () => {
    const file = join(dir, "yesterday.jsonl");
    await writeFile(file, "");

    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "memory",
      phase: "main",
      process: async () => {},
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);
    trunkState.promoteToActive({
      sessionFile: file,
      day: dayOf("2026-06-14T10:00:00Z"),
      openedAt: "2026-06-14T10:00:00Z",
    });

    // Recovery runs with no channel attached → lifecycle status is buffered, not lost.
    await coordinator.recoverStaleTrunks();
    expect(trunkState.listUnclosed()).toEqual([]);

    const lifecycleStatus = vi.fn(async () => {});
    coordinator.attachChannel(stubChannel(lifecycleStatus));

    // The flush is fire-and-forget from attachChannel; wait for both lines to land in order.
    await vi.waitFor(() => expect(lifecycleStatus).toHaveBeenCalledTimes(2));
    expect(lifecycleStatus.mock.calls.map((c) => c[0])).toEqual([
      "Post-processing: memory…",
      "Trunk closed",
    ]);
    // The first line opens a fresh message; the final edits it in place.
    expect(lifecycleStatus.mock.calls[0]?.[1]).toBe(true);
    expect(lifecycleStatus.mock.calls[1]?.[1]).toBe(false);
  });

  it("flushes a separate fresh message per recovered trunk", async () => {
    const a = join(dir, "day1.jsonl");
    const b = join(dir, "day2.jsonl");
    await writeFile(a, "");
    await writeFile(b, "");

    const regs = createRegistrations();
    regs.postProcessors.push({
      name: "memory",
      phase: "main",
      process: async () => {},
    });

    const now = () => new Date("2026-06-15T10:00:00Z");
    const { coordinator, trunkState } = makeCoordinator(db, createAgent(), regs, now);
    trunkState.addUnclosed(a);
    trunkState.addUnclosed(b);

    await coordinator.recoverStaleTrunks();

    const lifecycleStatus = vi.fn(async () => {});
    coordinator.attachChannel(stubChannel(lifecycleStatus));

    // Two trunks → two fresh messages (a fresh call starts each), each ending at "Trunk closed".
    await vi.waitFor(() => expect(lifecycleStatus).toHaveBeenCalledTimes(4));
    expect(lifecycleStatus.mock.calls.map((c) => c[0])).toEqual([
      "Post-processing: memory…",
      "Trunk closed",
      "Post-processing: memory…",
      "Trunk closed",
    ]);
    expect(lifecycleStatus.mock.calls[0]?.[1]).toBe(true); // trunk A: fresh
    expect(lifecycleStatus.mock.calls[1]?.[1]).toBe(false); // trunk A: final
    expect(lifecycleStatus.mock.calls[2]?.[1]).toBe(true); // trunk B: fresh
    expect(lifecycleStatus.mock.calls[3]?.[1]).toBe(false); // trunk B: final
  });
});
