import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Logger } from "../../src/log.ts";
import { walkBranches } from "../../src/sessions/branch-walk.ts";
import type { BranchRecord } from "../../src/sessions/trunk.ts";
import { fileExists } from "../../src/util/markdown-store.ts";

const fakeLog = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
} as unknown as Logger;

let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "tachi-branchwalk-"));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
  vi.clearAllMocks();
});

const DAY = "2026-06-15";

const records = (n: number): BranchRecord[] =>
  Array.from({ length: n }, (_, k) => ({
    branchId: `topic-${k + 1}`,
    originalLeafId: `leaf-${k + 1}`,
    baseId: k === 0 ? null : `sum-${k}`,
    summaryEntryId: `sum-${k + 1}`,
    lastExchange: null,
  }));

// The walk contract leaves the session itself untouched (markers go through the caller-supplied
// pair), so a bare object suffices as the AgentSession stand-in.
const makeSession = (): AgentSession => ({}) as unknown as AgentSession;

/** In-memory marker pair keyed however the caller chooses — mirroring the real trunk markers. */
const markerPair = (key: (record: BranchRecord) => string) => {
  const marked = new Set<string>();

  return {
    marked,
    isDone: (_session: AgentSession, record: BranchRecord) => marked.has(key(record)),
    markDone: (_session: AgentSession, record: BranchRecord) => {
      marked.add(key(record));
    },
  };
};

const fakeAgent = (cutPath: (leafId: string) => string | undefined) => ({
  forkAndContinue: vi.fn().mockResolvedValue(undefined),
  branchFile: vi.fn((_sourceFile: string, leafId: string) => cutPath(leafId)),
});

const progress = {
  start: (i: number, n: number) => `start ${i}/${n}`,
  done: (i: number, n: number) => `done ${i}/${n}`,
  failed: (i: number, n: number) => `failed ${i}/${n}`,
};

const defer = () => {
  let released = false;
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = () => {
      released = true;
      resolve();
    };
  });
  return { promise, release, isPending: () => !released };
};

describe("walkBranches", () => {
  it("skips records already marked done — no cut, no body, no progress line", async () => {
    const agent = fakeAgent((leafId) => join(dir, `branch-${leafId}.jsonl`));
    const markers = markerPair((record) => record.branchId);
    markers.marked.add("topic-1"); // a prior run already completed this branch
    const body = vi.fn().mockResolvedValue(undefined);
    const status = vi.fn();

    const { failed } = await walkBranches(
      makeSession(),
      join(dir, "trunk.jsonl"),
      records(2),
      DAY,
      {
        agent,
        body,
        isDone: markers.isDone,
        markDone: markers.markDone,
        log: fakeLog,
        status,
        progress,
      },
    );

    expect(failed).toEqual([]);
    expect(agent.branchFile).toHaveBeenCalledTimes(1);
    expect(agent.branchFile).toHaveBeenCalledWith(join(dir, "trunk.jsonl"), "leaf-2");
    expect(body).toHaveBeenCalledTimes(1);
    expect(status.mock.calls.map((call) => call[0])).toEqual(["start 2/2", "done 2/2"]);
  });

  it("treats a failed cut as warn + skip, not a failure", async () => {
    const agent = fakeAgent((leafId) =>
      leafId === "leaf-1" ? undefined : join(dir, `branch-${leafId}.jsonl`),
    );
    const markers = markerPair((record) => record.branchId);
    const body = vi.fn().mockResolvedValue(undefined);
    const status = vi.fn();

    const { failed } = await walkBranches(
      makeSession(),
      join(dir, "trunk.jsonl"),
      records(2),
      DAY,
      {
        agent,
        body,
        isDone: markers.isDone,
        markDone: markers.markDone,
        log: fakeLog,
        status,
        progress,
      },
    );

    expect(failed).toEqual([]); // a cut failure skipped the branch — nothing ran, nothing failed
    expect(body).toHaveBeenCalledTimes(1);
    expect(markers.marked.has("topic-1")).toBe(false);
    expect(fakeLog.warn).toHaveBeenCalled();
    expect(status.mock.calls.map((call) => call[0])).toEqual(["start 2/2", "done 2/2"]);
  });

  it("emits no status lines when no progress templates are supplied", async () => {
    const agent = fakeAgent((leafId) => join(dir, `branch-${leafId}.jsonl`));
    const markers = markerPair((record) => record.branchId);
    const body = vi.fn().mockResolvedValue(undefined);
    const status = vi.fn();

    await walkBranches(makeSession(), join(dir, "trunk.jsonl"), records(1), DAY, {
      agent,
      body,
      isDone: markers.isDone,
      markDone: markers.markDone,
      log: fakeLog,
      status,
    });

    expect(status).not.toHaveBeenCalled();
  });

  it("deletes the branch file only after the body settles, and marks done only after the body", async () => {
    const branchFilePath = join(dir, "branch-leaf-1.jsonl");
    await writeFile(branchFilePath, "{}", "utf8");

    const agent = fakeAgent(() => branchFilePath);
    const markers = markerPair((record) => record.branchId);
    const gate = defer();
    let bodyEntered = false;
    let markedWhilePending = false;

    const body = vi.fn().mockImplementation(async () => {
      bodyEntered = true;
      await gate.promise;
    });
    const markDone = vi.fn().mockImplementation((_session: AgentSession, record: BranchRecord) => {
      if (gate.isPending()) markedWhilePending = true;
      markers.marked.add(record.branchId);
    });

    const walk = walkBranches(makeSession(), join(dir, "trunk.jsonl"), records(1), DAY, {
      agent,
      body,
      isDone: markers.isDone,
      markDone,
      log: fakeLog,
    });

    // The walk's synchronous prefix runs the body up to its gate, so by now the body is in flight
    // and pending: the branch file must still exist and no marker may have landed yet.
    expect(bodyEntered).toBe(true);
    await expect(fileExists(branchFilePath)).resolves.toBe(true);
    expect(markDone).not.toHaveBeenCalled();

    gate.release();

    const { failed } = await walk;

    expect(failed).toEqual([]);
    expect(markedWhilePending).toBe(false);
    await expect(fileExists(branchFilePath)).resolves.toBe(false);
    expect(markers.marked.has("topic-1")).toBe(true);
  });

  it("still deletes the branch file when the body throws, leaves it unmarked, and isolates the failure", async () => {
    const agent = fakeAgent((leafId) => join(dir, `branch-${leafId}.jsonl`));
    const markers = markerPair((record) => record.branchId);
    const body = vi.fn().mockImplementation(async (record: BranchRecord) => {
      if (record.branchId === "topic-2") throw new Error("boom");
    });

    const { failed } = await walkBranches(
      makeSession(),
      join(dir, "trunk.jsonl"),
      records(3),
      DAY,
      {
        agent,
        body,
        isDone: markers.isDone,
        markDone: markers.markDone,
        log: fakeLog,
        progress,
      },
    );

    expect(failed.map((record) => record.branchId)).toEqual(["topic-2"]);
    // The failing branch's file was cleaned up and it carries no marker; its siblings completed.
    await expect(fileExists(join(dir, "branch-leaf-2.jsonl"))).resolves.toBe(false);
    expect(markers.marked.has("topic-2")).toBe(false);
    expect([...markers.marked].sort()).toEqual(["topic-1", "topic-3"]);
    expect(fakeLog.error).toHaveBeenCalled();
  });

  it("numbers progress 1-based over all records", async () => {
    const agent = fakeAgent((leafId) => join(dir, `branch-${leafId}.jsonl`));
    const markers = markerPair((record) => record.branchId);
    const body = vi.fn().mockImplementation(async (record: BranchRecord) => {
      if (record.branchId === "topic-1") throw new Error("boom");
    });
    const status = vi.fn();

    await walkBranches(makeSession(), join(dir, "trunk.jsonl"), records(2), DAY, {
      agent,
      body,
      isDone: markers.isDone,
      markDone: markers.markDone,
      log: fakeLog,
      status,
      progress,
    });

    expect(status.mock.calls.map((call) => call[0])).toEqual([
      "start 1/2",
      "failed 1/2",
      "start 2/2",
      "done 2/2",
    ]);
  });

  it("two concurrent walks over the same session file: both marker sets land, neither deletes the other's branch file", async () => {
    const sessionFile = join(dir, "trunk.jsonl");
    await writeFile(sessionFile, "{}", "utf8");
    const rs = records(2);
    const session = makeSession();

    // Every branch file both walks will cut, pre-created so deletions are observable.
    for (const tag of ["a", "b"] as const) {
      for (const leaf of ["leaf-1", "leaf-2"]) {
        await writeFile(join(dir, `branch-${tag}-${leaf}.jsonl`), "{}", "utf8");
      }
    }

    const buildWalker = (tag: "a" | "b") => {
      const agent = fakeAgent((leafId) => join(dir, `branch-${tag}-${leafId}.jsonl`));
      const markers = markerPair((record) => `${tag}:${record.branchId}`);
      const gate = defer();
      const bodyCalls: string[] = [];
      const body = async (record: BranchRecord) => {
        bodyCalls.push(record.branchId);
        await gate.promise;
      };

      return { agent, markers, gate, body, bodyCalls };
    };

    const walkerA = buildWalker("a");
    const walkerB = buildWalker("b");

    const walkWith = (walker: ReturnType<typeof buildWalker>) =>
      walkBranches(session, sessionFile, rs, DAY, {
        agent: walker.agent,
        body: walker.body,
        isDone: walker.markers.isDone,
        markDone: walker.markers.markDone,
        log: fakeLog,
      });

    const walkA = walkWith(walkerA);
    const walkB = walkWith(walkerB);

    // Both walkers are inside their first branch's body, each pending on its own gate — each over
    // its OWN cut of the branch (the detached-manager rule), never a shared file.
    expect(walkerA.bodyCalls).toEqual(["topic-1"]);
    expect(walkerB.bodyCalls).toEqual(["topic-1"]);

    // Let A finish completely while B is still mid-body.
    walkerA.gate.release();
    const resultA = await walkA;

    expect(resultA.failed).toEqual([]);
    expect([...walkerA.markers.marked].sort()).toEqual(["a:topic-1", "a:topic-2"]);
    await expect(fileExists(join(dir, "branch-a-leaf-1.jsonl"))).resolves.toBe(false);
    await expect(fileExists(join(dir, "branch-a-leaf-2.jsonl"))).resolves.toBe(false);
    // A's cleanup never touched B's branch file — walk B is still pending on it.
    await expect(fileExists(join(dir, "branch-b-leaf-1.jsonl"))).resolves.toBe(true);

    walkerB.gate.release();
    const resultB = await walkB;

    expect(resultB.failed).toEqual([]);
    expect([...walkerB.markers.marked].sort()).toEqual(["b:topic-1", "b:topic-2"]);
    await expect(fileExists(join(dir, "branch-b-leaf-1.jsonl"))).resolves.toBe(false);
    await expect(fileExists(join(dir, "branch-b-leaf-2.jsonl"))).resolves.toBe(false);
  });
});
