import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AppDatabase } from "../../src/db/index.ts";
import { NOTIFY_EVENT, type NotifyPayload } from "../../src/extensions/notifications/payload.ts";
import {
  BackgroundRunner,
  type BackgroundSide,
  type ExecutorDeps,
  ExtractedGoalSchema,
  executeBackgroundInstance,
  extractGoal,
  MIN_GOAL_LENGTH,
  type NotifyEmitter,
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

interface FakeSession {
  sessionFile: string;
  messages: { role: string; content: { type: string; text: string }[] }[];
  prompt: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
}

interface FakeSide extends BackgroundSide {
  openBackgroundSession: ReturnType<typeof vi.fn>;
  classify: ReturnType<typeof vi.fn>;
  session: FakeSession;
}

/**
 * A `BackgroundSide` whose single persistent session appends an assistant turn per `prompt`.
 * `respond` returns the assistant text for each turn (or invokes a custom tool, e.g. ask_user);
 * `customTools` from the open call are forwarded so a prompt can drive the ask_user pause.
 */
const makeSide = (
  respond: (
    turn: number,
    tools: { name: string; execute: (id: string, params: unknown) => unknown }[],
  ) => Promise<string> | string,
  classify: ReturnType<typeof vi.fn>,
  sessionFile = "/sessions/bg-task.jsonl",
): FakeSide => {
  const session: FakeSession = {
    sessionFile,
    messages: [],
    prompt: vi.fn(),
    dispose: vi.fn(),
    abort: vi.fn(),
  };

  let tools: { name: string; execute: (id: string, params: unknown) => unknown }[] = [];
  let turn = 0;

  session.prompt.mockImplementation(async () => {
    turn += 1;
    const text = await respond(turn, tools);
    session.messages.push({ role: "assistant", content: [{ type: "text", text }] });
  });

  const openBackgroundSession = vi.fn(async (options: { customTools?: typeof tools }) => {
    tools = options.customTools ?? [];
    return session;
  });

  return { openBackgroundSession, classify, session } as FakeSide;
};

const makeDeps = (side: BackgroundSide, maxIterations = 10, maxConcurrent = 3) => {
  const emit = vi.fn<NotifyEmitter>();
  const runPostProcessors = vi.fn<ExecutorDeps["runPostProcessors"]>().mockResolvedValue(undefined);

  const deps: ExecutorDeps = {
    repository,
    side,
    emit,
    runPostProcessors,
    maxIterations,
    maxConcurrent,
    timezone: "UTC",
    now,
    log: fakeLog,
  };

  return { ...deps, emit, runPostProcessors };
};

const notifyPayloads = (emit: ReturnType<typeof vi.fn>): NotifyPayload[] =>
  emit.mock.calls
    .filter(([event]) => event === NOTIFY_EVENT)
    .map(([, payload]) => payload as NotifyPayload);

// A snapshotted goal skips run-start extraction (Step 2.2), so classify is exercised by
// the evaluator only — used by the evaluator/cancel tests that predate goal extraction and
// must keep their original classify-call semantics. Omit the goal (default) to exercise
// the null-goal extraction path.
const pendingInstance = (goal?: string): TaskInstanceRecord =>
  repository.createInstance({
    definitionId: null,
    taskType: "background",
    prompt: "summarize the inbox",
    scheduledFor: current,
    ...(goal != null ? { goal } : {}),
  });

// A goal long enough to clear extraction's MIN_GOAL_LENGTH heuristic, used wherever a
// snapshotted goal is meant to read as a real goal rather than a marker.
const SNAPSHOTTED_GOAL =
  "Summarize the inbox into a scannable digest of unread messages for the user.";

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("extractGoal", () => {
  const sideWith = (classify: ReturnType<typeof vi.fn>): BackgroundSide =>
    ({ classify }) as unknown as BackgroundSide;

  it("returns the trimmed goal when classify succeeds and it clears the minimum length", async () => {
    const goal = `   ${"x".repeat(MIN_GOAL_LENGTH)}   `;
    const classify = vi.fn().mockResolvedValue({ goal });

    expect(await extractGoal(sideWith(classify), "task prompt", fakeLog)).toBe(
      "x".repeat(MIN_GOAL_LENGTH),
    );

    expect(classify).toHaveBeenCalledWith(
      expect.objectContaining({ schema: ExtractedGoalSchema, user: "task prompt" }),
    );
  });

  it("returns null when classify throws (and logs the failure)", async () => {
    const classify = vi.fn().mockRejectedValue(new Error("model down"));

    expect(await extractGoal(sideWith(classify), "task prompt", fakeLog)).toBeNull();
    expect(fakeLog.warn).toHaveBeenCalled();
  });

  it("returns null for a goal below the minimum length", async () => {
    const classify = vi.fn().mockResolvedValue({ goal: "x".repeat(MIN_GOAL_LENGTH - 1) });

    expect(await extractGoal(sideWith(classify), "task prompt", fakeLog)).toBeNull();
  });
});

describe("executeBackgroundInstance", () => {
  it("reuses ONE persistent session across continuation iterations", async () => {
    const classify = vi
      .fn()
      .mockResolvedValueOnce({ status: "continue", reason: "announced next steps" })
      .mockResolvedValueOnce({ status: "complete", reason: "summarized 3 messages" });
    const side = makeSide(
      (turn) =>
        turn === 1 ? "working on it, next I will read the inbox" : "done: 3 messages summarized",
      classify,
    );
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    // The session is opened exactly once and prompted twice on the SAME session.
    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(side.session.prompt).toHaveBeenCalledTimes(2);
    expect(classify).toHaveBeenCalledTimes(2);

    // The session is opened with the composed background system prompt and the custom tools.
    const openArgs = side.openBackgroundSession.mock.calls[0]?.[0];
    expect(openArgs?.system as string).toContain("Current date and time:");
    expect(openArgs?.system as string).toContain("notify_user");
    // notify_user comes from the notifications extension (background-scoped); only ask_user
    // is a custom tool here.
    expect((openArgs?.customTools as { name: string }[]).map((t) => t.name)).toEqual(["ask_user"]);

    // The continuation prompt is a short nudge carrying the evaluator note — NOT an excerpt replay.
    const continuation = side.session.prompt.mock.calls[1]?.[0] as string;
    expect(continuation).toContain("announced next steps");
    expect(continuation).not.toContain("working on it");
    expect(continuation).not.toContain("summarize the inbox");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("summarized 3 messages");
    expect(completed?.startedAt).toEqual(current);
    expect(side.session.dispose).toHaveBeenCalled();

    // Successful completion emits no programmatic notice — the agent self-reports via
    // notify_user at its discretion, so the executor fires nothing on the "notify" event.
    expect(deps.emit).not.toHaveBeenCalled();
  });

  it("opens with the task prompt and runs post-processors with the transcript", async () => {
    const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });
    const side = makeSide(() => "report ready", classify, "/sessions/with-transcript.jsonl");
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    // Workspace context now arrives via each extension's background-scoped context section
    // (pi before_agent_start), not folded into the opening prompt — the prompt is just the task.
    const opening = side.session.prompt.mock.calls[0]?.[0] as string;
    expect(opening).toBe("summarize the inbox");

    // The session file is persisted and fed to post-processing so memory extraction reads it.
    expect(repository.getInstance(instance.id)?.piSessionFile).toBe(
      "/sessions/with-transcript.jsonl",
    );
    expect(deps.runPostProcessors).toHaveBeenCalledWith(
      expect.objectContaining({ transcriptPath: "/sessions/with-transcript.jsonl", trunk: null }),
    );
  });

  it("fails the instance when the evaluator reports an error", async () => {
    const classify = vi
      .fn()
      .mockResolvedValue({ status: "error", reason: "unrecoverable access error" });
    const side = makeSide(() => "I cannot access the inbox at all", classify);
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("Agent stuck: unrecoverable access error");
    expect(side.session.dispose).toHaveBeenCalled();

    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({
        text: expect.stringContaining("Task failed"),
        severity: "warning",
      }),
    );
  });

  it("fails after exhausting the iteration cap", async () => {
    const classify = vi
      .fn()
      .mockResolvedValue({ status: "continue", reason: "still mid-workflow" });
    const side = makeSide(() => "still going", classify);
    const deps = makeDeps(side, 2);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    expect(side.session.prompt).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.result).toBe(
      "Max iterations (2) reached without completion",
    );
    expect(side.session.dispose).toHaveBeenCalled();
    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({ severity: "warning" }),
    );
  });

  it("treats an evaluator crash as continue", async () => {
    const classify = vi
      .fn()
      .mockRejectedValueOnce(new Error("model down"))
      .mockResolvedValueOnce({ status: "complete", reason: "finished" });
    const side = makeSide((turn) => (turn === 1 ? "first pass" : "all done"), classify);
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    expect(side.session.prompt).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("pauses to waiting when the agent asks the user a question, persisting the session file", async () => {
    const classify = vi.fn();
    const side = makeSide(async (_turn, tools) => {
      const askUser = tools.find((tool) => tool.name === "ask_user");
      await askUser?.execute("call-1", { question: "Which inbox — work or personal?" });

      return "I need to know which inbox before I continue.";
    }, classify);
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    // The run paused before the evaluator ran, and the session was disposed on the pause.
    expect(classify).not.toHaveBeenCalled();
    expect(side.session.dispose).toHaveBeenCalled();

    const waiting = repository.getInstance(instance.id);
    expect(waiting?.status).toBe("waiting");
    expect(waiting?.question).toBe("Which inbox — work or personal?");
    expect(waiting?.userResponse).toBeNull();
    expect(waiting?.piSessionFile).toBe("/sessions/bg-task.jsonl");

    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({
        severity: "urgent",
        text: expect.stringContaining("Which inbox — work or personal?"),
      }),
    );
  });

  it("resumes a persistent session by reopening its file and prompting the user's reply", async () => {
    const classify = vi
      .fn()
      .mockResolvedValue({ status: "complete", reason: "summarized the work inbox" });
    const side = makeSide(() => "done — used the work inbox", classify, "/sessions/resumed.jsonl");
    const deps = makeDeps(side);

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      startedAt: current,
      question: "Which inbox?",
      resumeContext: "I paused to ask which inbox.",
      userResponse: "the work one",
      piSessionFile: "/sessions/resumed.jsonl",
    });

    await executeBackgroundInstance(
      deps,
      repository.getInstance(instance.id) as TaskInstanceRecord,
    );

    // The persistent session is reopened from its file — no fresh start, no excerpt replay.
    expect(side.openBackgroundSession).toHaveBeenCalledWith(
      expect.objectContaining({ sessionFile: "/sessions/resumed.jsonl" }),
    );
    const resumePrompt = side.session.prompt.mock.calls[0]?.[0] as string;
    expect(resumePrompt).toBe("the work one");
    expect(resumePrompt).not.toContain("I paused to ask which inbox.");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("summarized the work inbox");
    expect(completed?.question).toBeNull();
    expect(completed?.resumeContext).toBeNull();
  });

  it("resumes a legacy instance (no session file) by replaying the captured excerpt", async () => {
    const classify = vi
      .fn()
      .mockResolvedValue({ status: "complete", reason: "summarized the work inbox" });
    const side = makeSide(() => "done — used the work inbox", classify);
    const deps = makeDeps(side);

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      startedAt: current,
      question: "Which inbox?",
      resumeContext: "I paused to ask which inbox.",
      userResponse: "the work one",
      // No piSessionFile — predates persistent background sessions.
    });

    await executeBackgroundInstance(
      deps,
      repository.getInstance(instance.id) as TaskInstanceRecord,
    );

    // A legacy resume opens a FRESH session (no sessionFile) and replays the excerpt prompt.
    expect(side.openBackgroundSession).toHaveBeenCalledWith(
      expect.objectContaining({ sessionFile: null }),
    );
    const resumePrompt = side.session.prompt.mock.calls[0]?.[0] as string;
    expect(resumePrompt).toContain("Which inbox?");
    expect(resumePrompt).toContain("the work one");
    expect(resumePrompt).toContain("I paused to ask which inbox.");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("summarized the work inbox");
    expect(completed?.question).toBeNull();
    expect(completed?.resumeContext).toBeNull();
  });

  it("labels the source with the definition name when the instance has one", async () => {
    const classify = vi.fn().mockResolvedValue({ status: "error", reason: "blocked" });
    const side = makeSide(() => "cannot proceed", classify);
    const deps = makeDeps(side);

    const definition = repository.createDefinition({
      name: "Inbox Triage",
      schedule: { type: "cron", expression: "* * * * *" },
      taskType: "background",
      prompt: "triage the inbox",
    });
    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: "triage the inbox",
      scheduledFor: current,
    });

    await executeBackgroundInstance(deps, instance);

    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({ source: "Background task: Inbox Triage" }),
    );
  });

  it("records the session file as null when the opened session has none", async () => {
    const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });
    const side = makeSide(() => "all done", classify);
    // A session opened without a backing file forces the null transcript branch.
    side.session.sessionFile = null as unknown as string;
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    expect(repository.getInstance(instance.id)?.piSessionFile).toBeNull();
    expect(deps.runPostProcessors).toHaveBeenCalledWith(
      expect.objectContaining({ transcriptPath: null }),
    );
  });

  it("fails the instance when the run itself throws and disposes the session", async () => {
    const classify = vi.fn();
    const side = makeSide(() => {
      throw new Error("session exploded");
    }, classify);
    const deps = makeDeps(side);

    const instance = pendingInstance();
    await executeBackgroundInstance(deps, instance);

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toContain("session exploded");
    expect(side.session.dispose).toHaveBeenCalled();
    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({
        severity: "warning",
        source: expect.stringContaining("Background task"),
      }),
    );
  });

  it("never prompts when cancelled before the first iteration", async () => {
    const classify = vi.fn();
    const side = makeSide(() => "anything", classify);
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    const controller = new AbortController();
    controller.abort();

    await executeBackgroundInstance(deps, instance, controller.signal);

    expect(side.session.prompt).not.toHaveBeenCalled();
    expect(side.session.dispose).toHaveBeenCalled();
    // The cancel initiator owns the terminal write: the executor leaves its
    // initial `running` transition in place and emits no failure notice.
    expect(repository.getInstance(instance.id)?.status).toBe("running");
    expect(classify).not.toHaveBeenCalled();
    expect(notifyPayloads(deps.emit)).toHaveLength(0);
  });

  it("stops prompting and emits no failure when cancelled between iterations", async () => {
    const classify = vi.fn().mockResolvedValue({ status: "continue", reason: "more to do" });
    const side = makeSide(() => "still working", classify);
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    const controller = new AbortController();

    // After the first turn completes, cancel — the abort listener interrupts
    // and the loop bails before iteration 2.
    side.session.prompt.mockImplementationOnce(async () => {
      side.session.messages.push({
        role: "assistant",
        content: [{ type: "text", text: "turn one" }],
      });
      controller.abort();
    });

    await executeBackgroundInstance(deps, instance, controller.signal);

    expect(side.session.prompt).toHaveBeenCalledTimes(1);
    expect(side.session.abort).toHaveBeenCalledTimes(1);
    expect(side.session.dispose).toHaveBeenCalled();
    expect(repository.getInstance(instance.id)?.status).toBe("running");
    expect(classify).not.toHaveBeenCalled();
    expect(notifyPayloads(deps.emit)).toHaveLength(0);
  });

  describe("goal extraction at run start", () => {
    const triageDefinition = () =>
      repository.createDefinition({
        name: "Triage",
        schedule: { type: "cron", expression: "* * * * *" },
        taskType: "background",
        prompt: "triage the inbox",
      });

    it("extracts a goal when the instance has none and surfaces it in the opening prompt", async () => {
      const classify = vi
        .fn()
        .mockResolvedValueOnce({ goal: SNAPSHOTTED_GOAL }) // run-start extraction
        .mockResolvedValueOnce({ status: "complete", reason: "done" }); // evaluator
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const instance = pendingInstance(); // ad-hoc, no goal
      await executeBackgroundInstance(deps, instance);

      // classify ran once for extraction and once for the evaluator.
      expect(classify).toHaveBeenCalledTimes(2);

      // The extracted goal is persisted on the instance ...
      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);

      // ... and surfaced in the opening prompt (not the bare task).
      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("<goal>");
      expect(opening).toContain(SNAPSHOTTED_GOAL);
      expect(opening).toContain("summarize the inbox");
    });

    it("writes the extracted goal back to the definition when its goal is still null", async () => {
      const classify = vi
        .fn()
        .mockResolvedValueOnce({ goal: SNAPSHOTTED_GOAL })
        .mockResolvedValueOnce({ status: "complete", reason: "done" });
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const definition = triageDefinition();
      const instance = repository.createInstance({
        definitionId: definition.id,
        taskType: "background",
        prompt: "triage the inbox",
        scheduledFor: current,
      });
      await executeBackgroundInstance(deps, instance);

      // The definition goal was null, so the extracted goal is written back ...
      expect(repository.getDefinition(definition.id)?.goal).toBe(SNAPSHOTTED_GOAL);
      // ... and the instance carries it for this run.
      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);
    });

    it("does not extract when the instance already has a snapshotted goal", async () => {
      const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const instance = pendingInstance(SNAPSHOTTED_GOAL);
      await executeBackgroundInstance(deps, instance);

      // classify is the evaluator only — no extraction call.
      expect(classify).toHaveBeenCalledTimes(1);

      // The opening surfaces the snapshotted goal.
      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("<goal>");
      expect(opening).toContain(SNAPSHOTTED_GOAL);

      // The instance goal is unchanged.
      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);
    });

    it("skips write-back when the goal was set in the meantime, and the queued instance extracts its own goal", async () => {
      const classify = vi
        .fn()
        .mockResolvedValueOnce({ goal: SNAPSHOTTED_GOAL }) // this instance extracts its own
        .mockResolvedValueOnce({ status: "complete", reason: "done" });
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const definition = triageDefinition();
      // Instance snapshotted null — queued before the definition had a goal.
      const instance = repository.createInstance({
        definitionId: definition.id,
        taskType: "background",
        prompt: "triage the inbox",
        scheduledFor: current,
      });
      // Between creation and run start the definition goal is set (by update_task or an
      // earlier instance's write-back). This instance must neither inherit nor clobber it.
      const setMeanwhile = "Goal set by update_task while this instance was queued.";
      repository.updateDefinition(definition.id, { goal: setMeanwhile });

      await executeBackgroundInstance(deps, instance);

      // The definition goal is NOT clobbered by this run's extraction.
      expect(repository.getDefinition(definition.id)?.goal).toBe(setMeanwhile);
      // The instance used its OWN null snapshot and extracted its own goal for this run.
      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);
    });

    it("proceeds on the task-prompt basis when extraction throws, persisting nothing", async () => {
      const classify = vi
        .fn()
        .mockRejectedValueOnce(new Error("extraction model down")) // extraction fails
        .mockResolvedValueOnce({ status: "complete", reason: "done" }); // evaluator
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const definition = triageDefinition();
      const instance = repository.createInstance({
        definitionId: definition.id,
        taskType: "background",
        prompt: "triage the inbox",
        scheduledFor: current,
      });
      await executeBackgroundInstance(deps, instance);

      // Nothing persisted: both goals stay null.
      expect(repository.getInstance(instance.id)?.goal).toBeNull();
      expect(repository.getDefinition(definition.id)?.goal).toBeNull();

      // The opening is the bare task prompt (the goal-null path).
      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toBe("triage the inbox");
    });

    it("treats a too-short extracted goal as unusable and persists nothing", async () => {
      const classify = vi
        .fn()
        .mockResolvedValueOnce({ goal: "too short to be a real goal" }) // below MIN_GOAL_LENGTH
        .mockResolvedValueOnce({ status: "complete", reason: "done" });
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const instance = pendingInstance(); // ad-hoc, no goal
      await executeBackgroundInstance(deps, instance);

      expect(repository.getInstance(instance.id)?.goal).toBeNull();

      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toBe("summarize the inbox");
    });

    it("does not extract on a resuming run (the instance already has its goal)", async () => {
      const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });
      const side = makeSide(() => "done", classify);
      const deps = makeDeps(side);

      const instance = pendingInstance(SNAPSHOTTED_GOAL);
      repository.updateInstance(instance.id, {
        status: "waiting",
        startedAt: current,
        question: "Which inbox?",
        resumeContext: "paused to ask",
        userResponse: "the work one",
        piSessionFile: "/sessions/resumed.jsonl",
      });
      await executeBackgroundInstance(
        deps,
        repository.getInstance(instance.id) as TaskInstanceRecord,
      );

      // Resuming → no extraction; classify is the evaluator only.
      expect(classify).toHaveBeenCalledTimes(1);
    });
  });
});

describe("BackgroundRunner", () => {
  it("dispatches pending instances once and tracks them across ticks", async () => {
    const side = makeSide(
      () => "done",
      vi.fn().mockResolvedValue({ status: "complete", reason: "done" }),
    );
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();

    runner.tick();
    runner.tick();
    await runner.drain();

    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("dispatches a resumable waiting instance to continue it", async () => {
    const side = makeSide(
      () => "resumed and finished",
      vi.fn().mockResolvedValue({ status: "complete", reason: "finished after reply" }),
    );
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      question: "left or right?",
      resumeContext: "paused",
      userResponse: "left",
      piSessionFile: "/sessions/bg-task.jsonl",
    });

    runner.tick();
    await runner.drain();

    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("logs and clears in-flight when an executor rejects outside its own guard", async () => {
    const side = makeSide(
      () => "done",
      vi.fn().mockResolvedValue({ status: "complete", reason: "done" }),
    );
    const deps = makeDeps(side);

    const instance = pendingInstance();

    // updateInstance fires before executeBackgroundInstance's try block, so a throw
    // here escapes the executor and must be caught by the runner's own .catch.
    const failingRepository = {
      ...repository,
      getResumableInstances: () => [],
      getPendingInstances: () => [instance],
      getDefinition: () => null,
      updateInstance: () => {
        throw new Error("db write failed");
      },
    } as unknown as TaskRepository;

    const runner = new BackgroundRunner({ ...deps, repository: failingRepository });

    runner.tick();
    await runner.drain();

    expect(fakeLog.error).toHaveBeenCalledWith(
      expect.objectContaining({ instanceId: instance.id }),
      "background executor crashed",
    );
  });

  it("leaves a waiting instance without a response untouched", async () => {
    const side = makeSide(() => "", vi.fn());
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance();
    repository.updateInstance(instance.id, {
      status: "waiting",
      question: "?",
      resumeContext: "x",
    });

    runner.tick();
    await runner.drain();

    expect(side.openBackgroundSession).not.toHaveBeenCalled();
    expect(repository.getInstance(instance.id)?.status).toBe("waiting");
  });

  it("never runs more than maxConcurrent instances at once", async () => {
    let active = 0;
    let peak = 0;
    const releases: Array<() => void> = [];

    // Each dispatched instance opens its own session whose single prompt blocks until released.
    const openBackgroundSession = vi.fn(async () => {
      active += 1;
      peak = Math.max(peak, active);

      const prompt = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            releases.push(() => {
              active -= 1;
              resolve();
            });
          }),
      );

      return {
        sessionFile: "/sessions/concurrent.jsonl",
        messages: [{ role: "assistant", content: [{ type: "text", text: "done" }] }],
        prompt,
        dispose: vi.fn(),
      };
    });
    const classify = vi.fn().mockResolvedValue({ status: "complete", reason: "done" });

    const runner = new BackgroundRunner(
      makeDeps({ openBackgroundSession, classify } as unknown as BackgroundSide, 10, 2),
    );

    // A snapshotted goal skips run-start extraction so openBackgroundSession remains the
    // first await (the timing this concurrency assertion is calibrated to).
    for (let i = 0; i < 5; i += 1) pendingInstance(SNAPSHOTTED_GOAL);

    runner.tick();
    await Promise.resolve();

    // Only the cap is dispatched; the other three pending instances wait.
    expect(active).toBe(2);
    expect(openBackgroundSession).toHaveBeenCalledTimes(2);

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
    expect(openBackgroundSession).toHaveBeenCalledTimes(5);
  });

  it("cancel() aborts an in-flight run and awaits its unwind", async () => {
    // A prompt that blocks until released, so the run is genuinely mid-flight.
    let release: () => void = () => {};
    const prompt = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const dispose = vi.fn();
    const sessionAbort = vi.fn(() => release());
    const openBackgroundSession = vi.fn(async () => ({
      sessionFile: "/sessions/bg-task.jsonl",
      messages: [],
      prompt,
      dispose,
      abort: sessionAbort,
    }));
    const deps = makeDeps({
      openBackgroundSession,
      classify: vi.fn(),
    } as unknown as BackgroundSide);
    const runner = new BackgroundRunner(deps);

    const instance = pendingInstance();
    runner.tick();
    expect(repository.getInstance(instance.id)?.status).toBe("running");

    // Let the run reach its blocking prompt before cancelling.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(prompt).toHaveBeenCalledTimes(1);

    const cancelled = await runner.cancel(instance.id);

    expect(cancelled).toBe(true);
    expect(sessionAbort).toHaveBeenCalledTimes(1);
    expect(dispose).toHaveBeenCalled();
    // The executor's abort path writes no terminal status and emits no notice —
    // the cancel initiator (here omitted) owns the terminal write.
    expect(repository.getInstance(instance.id)?.status).toBe("running");
    expect(notifyPayloads(deps.emit)).toHaveLength(0);
  });

  it("cancel() reports false for an instance not running in this process", async () => {
    const side = makeSide(
      () => "done",
      vi.fn().mockResolvedValue({ status: "complete", reason: "done" }),
    );
    const runner = new BackgroundRunner(makeDeps(side));

    const cancelled = await runner.cancel("not-in-flight");

    expect(cancelled).toBe(false);
  });
});
