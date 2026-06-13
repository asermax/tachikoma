import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Delivery } from "../../src/channels/types.ts";
import type { AppDatabase } from "../../src/db/index.ts";
import {
  BackgroundRunner,
  type BackgroundSide,
  type ExecutorDeps,
  executeBackgroundInstance,
  type TaskNotification,
} from "../../src/extensions/tasks/executor.ts";
import { TaskRepository } from "../../src/extensions/tasks/repository.ts";
import type { TaskInstanceRecord } from "../../src/extensions/tasks/schema.ts";
import type { Logger } from "../../src/log.ts";
import { createTasksTestDb } from "./setup.ts";

const fakeLog = {
  error: vi.fn(),
  warn: vi.fn(),
  info: vi.fn(),
  debug: vi.fn(),
} as unknown as Logger;

let db: AppDatabase;
let current: Date;
let repository: TaskRepository;

const now = () => current;

const makeDeps = (side: BackgroundSide, maxIterations = 10, maxConcurrent = 3) => {
  const deliver = vi.fn<(delivery: Delivery) => void>();
  const notify = vi.fn<(notification: TaskNotification) => void>();

  const deps: ExecutorDeps = {
    repository,
    side,
    deliver,
    notify,
    maxIterations,
    maxConcurrent,
    timezone: "UTC",
    now,
    log: fakeLog,
  };

  return { ...deps, deliver, notify };
};

const pendingInstance = (): TaskInstanceRecord =>
  repository.createInstance({
    definitionId: null,
    taskType: "background",
    prompt: "summarize the inbox",
    scheduledFor: current,
  });

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("executeBackgroundInstance", () => {
  it("loops until the evaluator reports completion", async () => {
    const run = vi
      .fn()
      .mockResolvedValueOnce({ text: "working on it, next I will read the inbox" })
      .mockResolvedValueOnce({ text: "done: 3 messages summarized" });
    const classify = vi
      .fn()
      .mockResolvedValueOnce({ status: "continue", reason: "announced next steps" })
      .mockResolvedValueOnce({ status: "complete", reason: "summarized 3 messages" });
    const deps = makeDeps({ run, classify });

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    expect(run).toHaveBeenCalledTimes(2);
    expect(classify).toHaveBeenCalledTimes(2);

    // The run uses the composed background system prompt (autonomous role + shared hygiene).
    const system = run.mock.calls[0]?.[0]?.system as string;
    expect(system).toContain("Current date and time:");
    expect(system).toContain("notify_user");

    // The continuation prompt carries the task, previous progress, and reason.
    const continuation = run.mock.calls[1]?.[0]?.prompt as string;
    expect(continuation).toContain("summarize the inbox");
    expect(continuation).toContain("working on it");
    expect(continuation).toContain("announced next steps");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("summarized 3 messages");
    expect(completed?.startedAt).toEqual(current);

    expect(deps.deliver).toHaveBeenCalledWith(
      expect.objectContaining({
        gate: "idle",
        text: expect.stringContaining("done: 3 messages summarized"),
      }),
    );
    expect(deps.notify).toHaveBeenCalledWith(
      expect.objectContaining({ instanceId: instance.id, status: "completed" }),
    );
  });

  it("fails the instance when the evaluator reports an error", async () => {
    const side: BackgroundSide = {
      run: vi.fn().mockResolvedValue({ text: "I cannot access the inbox at all" }),
      classify: vi
        .fn()
        .mockResolvedValue({ status: "error", reason: "unrecoverable access error" }),
    };
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("Agent stuck: unrecoverable access error");

    expect(deps.deliver).toHaveBeenCalledWith(
      expect.objectContaining({ text: expect.stringContaining("Task failed") }),
    );
    expect(deps.notify).toHaveBeenCalledWith(expect.objectContaining({ status: "failed" }));
  });

  it("fails after exhausting the iteration cap", async () => {
    const side: BackgroundSide = {
      run: vi.fn().mockResolvedValue({ text: "still going" }),
      classify: vi.fn().mockResolvedValue({ status: "continue", reason: "still mid-workflow" }),
    };
    const deps = makeDeps(side, 2);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    expect(side.run).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.result).toBe(
      "Max iterations (2) reached without completion",
    );
    expect(deps.notify).toHaveBeenCalledWith(expect.objectContaining({ status: "failed" }));
  });

  it("treats an evaluator crash as continue", async () => {
    const run = vi
      .fn()
      .mockResolvedValueOnce({ text: "first pass" })
      .mockResolvedValueOnce({ text: "all done" });
    const classify = vi
      .fn()
      .mockRejectedValueOnce(new Error("model down"))
      .mockResolvedValueOnce({ status: "complete", reason: "finished" });
    const deps = makeDeps({ run, classify });

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    expect(run).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("pauses to waiting when the agent asks the user a question", async () => {
    const run = vi.fn().mockImplementation(async ({ customTools }) => {
      const askUser = customTools?.find((tool: { name: string }) => tool.name === "ask_user");
      await askUser?.execute("call-1", { question: "Which inbox — work or personal?" });

      return { text: "I need to know which inbox before I continue." };
    });
    const classify = vi.fn();
    const deps = makeDeps({ run, classify });

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    // The run paused before the evaluator ran.
    expect(classify).not.toHaveBeenCalled();

    const waiting = repository.getInstance(instance.id);
    expect(waiting?.status).toBe("waiting");
    expect(waiting?.question).toBe("Which inbox — work or personal?");
    expect(waiting?.resumeContext).toBe("I need to know which inbox before I continue.");
    expect(waiting?.userResponse).toBeNull();

    expect(deps.deliver).toHaveBeenCalledWith(
      expect.objectContaining({
        gate: "immediate",
        text: expect.stringContaining("Which inbox — work or personal?"),
      }),
    );
    expect(deps.notify).toHaveBeenCalledWith(
      expect.objectContaining({ instanceId: instance.id, status: "waiting" }),
    );
  });

  it("resumes a waiting instance once the user has responded", async () => {
    const run = vi.fn().mockResolvedValue({ text: "done — used the work inbox" });
    const classify = vi
      .fn()
      .mockResolvedValue({ status: "complete", reason: "summarized the work inbox" });
    const deps = makeDeps({ run, classify });

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      startedAt: current,
      question: "Which inbox?",
      resumeContext: "I paused to ask which inbox.",
      userResponse: "the work one",
    });

    await executeBackgroundInstance(
      deps,
      repository.getInstance(instance.id) as TaskInstanceRecord,
    );

    // The resume prompt replays the captured progress, the question, and the reply.
    const resumePrompt = run.mock.calls[0]?.[0]?.prompt as string;
    expect(resumePrompt).toContain("Which inbox?");
    expect(resumePrompt).toContain("the work one");
    expect(resumePrompt).toContain("I paused to ask which inbox.");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("summarized the work inbox");
    // Resume bookkeeping is cleared once the run finishes.
    expect(completed?.question).toBeNull();
    expect(completed?.resumeContext).toBeNull();
  });

  it("fails the instance when the run itself throws", async () => {
    const side: BackgroundSide = {
      run: vi.fn().mockRejectedValue(new Error("session exploded")),
      classify: vi.fn(),
    };
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toContain("session exploded");
    expect(deps.deliver).toHaveBeenCalled();
  });
});

describe("BackgroundRunner", () => {
  it("dispatches pending instances once and tracks them across ticks", async () => {
    const side: BackgroundSide = {
      run: vi.fn().mockResolvedValue({ text: "done" }),
      classify: vi.fn().mockResolvedValue({ status: "complete", reason: "done" }),
    };
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();

    runner.tick();
    runner.tick();
    await runner.drain();

    expect(side.run).toHaveBeenCalledTimes(1);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("dispatches a resumable waiting instance to continue it", async () => {
    const side: BackgroundSide = {
      run: vi.fn().mockResolvedValue({ text: "resumed and finished" }),
      classify: vi.fn().mockResolvedValue({ status: "complete", reason: "finished after reply" }),
    };
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      question: "left or right?",
      resumeContext: "paused",
      userResponse: "left",
    });

    runner.tick();
    await runner.drain();

    expect(side.run).toHaveBeenCalledTimes(1);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("leaves a waiting instance without a response untouched", async () => {
    const side: BackgroundSide = {
      run: vi.fn(),
      classify: vi.fn(),
    };
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      question: "?",
      resumeContext: "x",
    });

    runner.tick();
    await runner.drain();

    expect(side.run).not.toHaveBeenCalled();
    expect(repository.getInstance(instance.id)?.status).toBe("waiting");
  });

  it("never runs more than maxConcurrent instances at once", async () => {
    let active = 0;
    let peak = 0;
    const releases: Array<() => void> = [];

    const run = vi.fn().mockImplementation(() => {
      active += 1;
      peak = Math.max(peak, active);

      return new Promise<{ text: string }>((resolve) => {
        releases.push(() => {
          active -= 1;
          resolve({ text: "done" });
        });
      });
    });
    const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });

    const runner = new BackgroundRunner(makeDeps({ run, classify }, 10, 2));

    for (let i = 0; i < 5; i += 1) pendingInstance();

    runner.tick();
    await Promise.resolve();

    // Only the cap is dispatched; the other three pending instances wait.
    expect(active).toBe(2);
    expect(run).toHaveBeenCalledTimes(2);

    // Free both slots; a later tick fills them with the next pending instances.
    releases.shift()?.();
    releases.shift()?.();
    await new Promise((resolve) => setTimeout(resolve, 0));

    runner.tick();
    await Promise.resolve();
    expect(active).toBe(2);

    while (releases.length > 0) {
      releases.shift()?.();
      await new Promise((resolve) => setTimeout(resolve, 0));
      runner.tick();
      await Promise.resolve();
    }

    await runner.drain();

    expect(peak).toBeLessThanOrEqual(2);
    expect(run).toHaveBeenCalledTimes(5);
  });
});
