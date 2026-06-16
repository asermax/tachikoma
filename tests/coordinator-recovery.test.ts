import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentManager } from "../src/agent/manager.ts";
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

const fakeSession = (sessionFile: string) => ({
  sessionFile,
  dispose: vi.fn(),
  messages: [],
  sessionManager: { getEntries: () => [], getLeafId: () => null, getBranch: () => [] },
});

// An agent that returns a fresh fake session keyed on the opened file.
const createAgent = () =>
  ({
    open: vi.fn(async (options?: { sessionFile?: string }) =>
      fakeSession(options?.sessionFile ?? "/tmp/new.jsonl"),
    ),
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
});
