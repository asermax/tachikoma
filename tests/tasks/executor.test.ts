import { beforeEach, describe, expect, it, vi } from "vitest";

import { FILE_READ_TOOLS } from "../../src/agent/file-tools.ts";
import type { AppDatabase } from "../../src/db/index.ts";
import { NOTIFY_EVENT, type NotifyPayload } from "../../src/extensions/notifications/payload.ts";
import {
  BackgroundRunner,
  type BackgroundSide,
  type ExecutorDeps,
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

// A custom tool as the test harness sees it: a name plus its execute handler. The harness
// forwards the customTools from openBackgroundSession into each `respond` callback so a test
// can drive update_goal / ask_user exactly as pi would invoke them during a turn.
interface HarnessTool {
  name: string;
  execute: (id: string, params: unknown) => unknown;
}

interface FakeSession {
  sessionFile: string;
  messages: { role: string; content: { type: string; text: string }[] }[];
  prompt: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
}

interface FakeSide extends BackgroundSide {
  openBackgroundSession: ReturnType<typeof vi.fn>;
  run: ReturnType<typeof vi.fn>;
  session: FakeSession;
}

/**
 * A `BackgroundSide` whose single persistent session appends an assistant turn per `prompt`.
 * `respond` returns the assistant text for each turn (or invokes a custom tool, e.g. ask_user);
 * `customTools` from the open call are forwarded so a prompt can drive ask_user / update_goal.
 */
const makeSide = (
  respond: (turn: number, tools: HarnessTool[]) => Promise<string> | string,
  run: ReturnType<typeof vi.fn>,
  sessionFile = "/sessions/bg-task.jsonl",
): FakeSide => {
  const session: FakeSession = {
    sessionFile,
    messages: [],
    prompt: vi.fn(),
    dispose: vi.fn(),
    abort: vi.fn(),
  };

  let tools: HarnessTool[] = [];
  let turn = 0;

  session.prompt.mockImplementation(async () => {
    turn += 1;
    const text = await respond(turn, tools);
    session.messages.push({ role: "assistant", content: [{ type: "text", text }] });
  });

  const openBackgroundSession = vi.fn(async (options: { customTools?: HarnessTool[] }) => {
    tools = options.customTools ?? [];
    return session;
  });

  return { openBackgroundSession, run, session } as FakeSide;
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

// A snapshotted goal skips run-start extraction (Step 2.2), so run is never called — used
// by the declaration/cancel tests that exercise the self-declaration loop, not extraction.
// Omit the goal (default) to exercise the null-goal extraction path.
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

const findTool = (tools: HarnessTool[], name: string): HarnessTool | undefined =>
  tools.find((tool) => tool.name === name);

// Drives a `completed` declaration through the customTools harness on the turn it is returned.
const declareCompleted =
  (summary: string, evidence = "inbox digest written, 3 unread items surfaced") =>
  async (_turn: number, tools: HarnessTool[]): Promise<string> => {
    await findTool(tools, "update_goal")?.execute("call-1", {
      status: "completed",
      goalRestated: SNAPSHOTTED_GOAL,
      evidence,
      summary,
    });
    return `done: ${summary}`;
  };

beforeEach(async () => {
  db = await createTasksTestDb();
  current = new Date("2026-06-12T10:00:00Z");
  repository = new TaskRepository(db, now);
});

describe("extractGoal", () => {
  const sideWith = (run: ReturnType<typeof vi.fn>): BackgroundSide =>
    ({ run }) as unknown as BackgroundSide;

  it("returns the trimmed goal when the run succeeds and it clears the minimum length", async () => {
    const goal = `   ${"x".repeat(MIN_GOAL_LENGTH)}   `;
    const run = vi.fn().mockResolvedValue({ text: goal });

    expect(await extractGoal(sideWith(run), "task prompt", fakeLog)).toBe(
      "x".repeat(MIN_GOAL_LENGTH),
    );

    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: "task prompt",
        tools: FILE_READ_TOOLS,
        tier: "processor",
        isolatePrompt: true,
      }),
    );
  });

  it("returns null when the run throws (and logs the failure)", async () => {
    const run = vi.fn().mockRejectedValue(new Error("model down"));

    expect(await extractGoal(sideWith(run), "task prompt", fakeLog)).toBeNull();
    expect(fakeLog.warn).toHaveBeenCalled();
  });

  it("returns null for a goal below the minimum length", async () => {
    const run = vi.fn().mockResolvedValue({ text: "x".repeat(MIN_GOAL_LENGTH - 1) });

    expect(await extractGoal(sideWith(run), "task prompt", fakeLog)).toBeNull();
  });
});

describe("executeBackgroundInstance", () => {
  it("completes when the agent declares completed, sourcing result from the summary", async () => {
    const side = makeSide(declareCompleted("3 messages summarized"), vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(side.session.prompt).toHaveBeenCalledTimes(1);

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("3 messages summarized");
    expect(completed?.question).toBeNull();
    expect(completed?.resumeContext).toBeNull();
    expect(side.session.dispose).toHaveBeenCalled();

    // Successful completion emits no programmatic notice — the agent self-reports via
    // notify_user at its discretion, so the executor fires nothing on the "notify" event.
    expect(deps.emit).not.toHaveBeenCalled();
  });

  it("runs post-processors with the transcript after a completed declaration", async () => {
    const side = makeSide(declareCompleted("done"), vi.fn(), "/sessions/with-transcript.jsonl");
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    expect(repository.getInstance(instance.id)?.piSessionFile).toBe(
      "/sessions/with-transcript.jsonl",
    );
    expect(deps.runPostProcessors).toHaveBeenCalledWith(
      expect.objectContaining({ transcriptPath: "/sessions/with-transcript.jsonl", trunk: null }),
    );
  });

  it("fails with the agent's reason when the agent declares not_completable", async () => {
    const side = makeSide(async (_turn, tools) => {
      await findTool(tools, "update_goal")?.execute("call-1", {
        status: "not_completable",
        reason: "no inbox access credentials",
      });
      return "cannot proceed";
    }, vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    const failed = repository.getInstance(instance.id);
    expect(failed?.status).toBe("failed");
    expect(failed?.result).toBe("no inbox access credentials");
    expect(side.session.dispose).toHaveBeenCalled();

    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({
        text: expect.stringContaining("no inbox access credentials"),
        severity: "warning",
      }),
    );
  });

  it("injects the completion nudge on a non-terminal turn, then completes on the next", async () => {
    const side = makeSide(async (turn, tools) => {
      if (turn === 1) return "still working — reading the inbox next"; // no declaration
      await findTool(tools, "update_goal")?.execute("call-2", {
        status: "completed",
        goalRestated: SNAPSHOTTED_GOAL,
        evidence: "digest written",
        summary: "3 messages summarized",
      });
      return "done";
    }, vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    // Two prompts on the SAME persistent session: the first turn ends without a declaration,
    // so the loop injects the nudge as the second prompt; the agent then declares completed.
    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(side.session.prompt).toHaveBeenCalledTimes(2);

    const nudge = side.session.prompt.mock.calls[1]?.[0] as string;
    expect(nudge).toContain("Evaluate whether your goal is complete");
    expect(nudge).toContain("update_goal");

    const completed = repository.getInstance(instance.id);
    expect(completed?.status).toBe("completed");
    expect(completed?.result).toBe("3 messages summarized");
  });

  it("does not terminate on an incomplete declaration — the agent retries within the run", async () => {
    const side = makeSide(async (turn, tools) => {
      const updateGoal = findTool(tools, "update_goal");
      if (turn === 1) {
        // Incomplete: evidence omitted. pi surfaces the thrown tool error to the agent, which
        // self-corrects within the SAME run — model pi's catch by swallowing the rejection here.
        // The flag must NOT be set, so the run continues.
        try {
          await updateGoal?.execute("call-1", {
            status: "completed",
            goalRestated: SNAPSHOTTED_GOAL,
            // evidence intentionally omitted
            summary: "done",
          });
          throw new Error("expected the incomplete declaration to be rejected");
        } catch (error) {
          expect((error as Error).message).toContain("evidence");
        }
        return "I omitted the evidence; retrying with it.";
      }
      await updateGoal?.execute("call-2", {
        status: "completed",
        goalRestated: SNAPSHOTTED_GOAL,
        evidence: "digest file written",
        summary: "done",
      });
      return "done";
    }, vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    // The incomplete call did not terminate the run — a nudge was injected and the agent
    // declared completed on the second turn (two prompts, then completed with the valid summary).
    expect(side.session.prompt).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
    expect(repository.getInstance(instance.id)?.result).toBe("done");
  });

  it("fails after exhausting the iteration cap without a terminal declaration", async () => {
    // The agent never declares and never asks — every turn is non-terminal, so the loop
    // nudges until the cap, then fails at the single automatic fail-point.
    const side = makeSide(() => "still going", vi.fn());
    const deps = makeDeps(side, 2);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    expect(side.session.prompt).toHaveBeenCalledTimes(2);
    expect(repository.getInstance(instance.id)?.status).toBe("failed");
    expect(repository.getInstance(instance.id)?.result).toBe(
      "Max iterations (2) reached without a terminal declaration",
    );
    expect(side.session.dispose).toHaveBeenCalled();
    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({ severity: "warning" }),
    );
  });

  it("pauses to waiting when the agent asks the user a question, persisting the session file", async () => {
    const side = makeSide(async (_turn, tools) => {
      const askUser = findTool(tools, "ask_user");
      await askUser?.execute("call-1", { question: "Which inbox — work or personal?" });

      return "I need to know which inbox before I continue.";
    }, vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    // The run paused before any second prompt — the nudge is NOT injected on an ask_user turn.
    expect(side.session.prompt).toHaveBeenCalledTimes(1);
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
    const side = makeSide(
      declareCompleted("summarized the work inbox"),
      vi.fn(),
      "/sessions/resumed.jsonl",
    );
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
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
    const side = makeSide(declareCompleted("summarized the work inbox"), vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
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
    const side = makeSide(async (_turn, tools) => {
      await findTool(tools, "update_goal")?.execute("call-1", {
        status: "not_completable",
        reason: "blocked",
      });
      return "cannot proceed";
    }, vi.fn());
    const deps = makeDeps(side);

    const definition = repository.createDefinition({
      name: "Inbox Triage",
      schedule: { type: "cron", expression: "* * * * *" },
      taskType: "background",
      prompt: "triage the inbox",
      goal: SNAPSHOTTED_GOAL,
    });
    const instance = repository.createInstance({
      definitionId: definition.id,
      taskType: "background",
      prompt: "triage the inbox",
      goal: SNAPSHOTTED_GOAL,
      scheduledFor: current,
    });

    await executeBackgroundInstance(deps, instance);

    expect(notifyPayloads(deps.emit)).toContainEqual(
      expect.objectContaining({ source: "Background task: Inbox Triage" }),
    );
  });

  it("records the session file as null when the opened session has none", async () => {
    const side = makeSide(declareCompleted("done"), vi.fn());
    // A session opened without a backing file forces the null transcript branch.
    side.session.sessionFile = null as unknown as string;
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
    await executeBackgroundInstance(deps, instance);

    expect(repository.getInstance(instance.id)?.piSessionFile).toBeNull();
    expect(deps.runPostProcessors).toHaveBeenCalledWith(
      expect.objectContaining({ transcriptPath: null }),
    );
  });

  it("fails the instance when the run itself throws and disposes the session", async () => {
    const side = makeSide(() => {
      throw new Error("session exploded");
    }, vi.fn());
    const deps = makeDeps(side);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
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
    const side = makeSide(() => "anything", vi.fn());
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
    expect(notifyPayloads(deps.emit)).toHaveLength(0);
  });

  it("stops prompting and emits no failure when cancelled between iterations", async () => {
    const side = makeSide(() => "still working", vi.fn());
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
    expect(notifyPayloads(deps.emit)).toHaveLength(0);
  });

  describe("opening prompt", () => {
    it("surfaces the task, the goal, and the declare instruction when a goal is present", async () => {
      const side = makeSide(declareCompleted("done"), vi.fn());
      const deps = makeDeps(side);

      const instance = pendingInstance(SNAPSHOTTED_GOAL);
      await executeBackgroundInstance(deps, instance);

      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("<task>");
      expect(opening).toContain("summarize the inbox");
      expect(opening).toContain("<goal>");
      expect(opening).toContain(SNAPSHOTTED_GOAL);
      // The declare instruction is appended in the goal-present branch too.
      expect(opening).toContain("update_goal");
      expect(opening).toContain('status="completed"');
    });
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
      // The extraction run derives the goal (its text output); then the agent declares.
      const run = vi.fn().mockResolvedValueOnce({ text: SNAPSHOTTED_GOAL });
      const side = makeSide(declareCompleted("done"), run);
      const deps = makeDeps(side);

      const instance = pendingInstance(); // ad-hoc, no goal
      await executeBackgroundInstance(deps, instance);

      // The extraction run fired exactly once (no post-turn evaluator).
      expect(run).toHaveBeenCalledTimes(1);

      // The extracted goal is persisted on the instance ...
      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);

      // ... and surfaced in the opening prompt alongside the declare instruction.
      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("<goal>");
      expect(opening).toContain(SNAPSHOTTED_GOAL);
      expect(opening).toContain("summarize the inbox");
      expect(opening).toContain("update_goal");
    });

    it("writes the extracted goal back to the definition when its goal is still null", async () => {
      const run = vi.fn().mockResolvedValueOnce({ text: SNAPSHOTTED_GOAL });
      const side = makeSide(declareCompleted("done"), run);
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
      const run = vi.fn();
      const side = makeSide(declareCompleted("done"), run);
      const deps = makeDeps(side);

      const instance = pendingInstance(SNAPSHOTTED_GOAL);
      await executeBackgroundInstance(deps, instance);

      // No extraction (snapshotted goal) → the extraction run is never called.
      expect(run).not.toHaveBeenCalled();

      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("<goal>");
      expect(opening).toContain(SNAPSHOTTED_GOAL);

      expect(repository.getInstance(instance.id)?.goal).toBe(SNAPSHOTTED_GOAL);
    });

    it("skips write-back when the goal was set in the meantime, and the queued instance extracts its own goal", async () => {
      const run = vi.fn().mockResolvedValueOnce({ text: SNAPSHOTTED_GOAL }); // this instance extracts its own
      const side = makeSide(declareCompleted("done"), run);
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
      const run = vi.fn().mockRejectedValueOnce(new Error("extraction model down")); // extraction fails
      const side = makeSide(declareCompleted("done"), run);
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

      // The opening carries the task and the declare instruction but NO goal block.
      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("triage the inbox");
      expect(opening).not.toContain("<goal>");
      expect(opening).toContain("update_goal");
    });

    it("treats a too-short extracted goal as unusable and persists nothing", async () => {
      const run = vi.fn().mockResolvedValueOnce({ text: "too short to be a real goal" }); // below MIN_GOAL_LENGTH
      const side = makeSide(declareCompleted("done"), run);
      const deps = makeDeps(side);

      const instance = pendingInstance(); // ad-hoc, no goal
      await executeBackgroundInstance(deps, instance);

      expect(repository.getInstance(instance.id)?.goal).toBeNull();

      const opening = side.session.prompt.mock.calls[0]?.[0] as string;
      expect(opening).toContain("summarize the inbox");
      expect(opening).not.toContain("<goal>");
    });

    it("does not extract on a resuming run (the instance already has its goal)", async () => {
      const run = vi.fn();
      const side = makeSide(declareCompleted("done"), run);
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

      // Resuming → no extraction → the extraction run is never called.
      expect(run).not.toHaveBeenCalled();
    });
  });
});

describe("BackgroundRunner", () => {
  it("dispatches pending instances once and tracks them across ticks", async () => {
    const side = makeSide(declareCompleted("done"), vi.fn());
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance(SNAPSHOTTED_GOAL);

    runner.tick();
    runner.tick();
    await runner.drain();

    expect(side.openBackgroundSession).toHaveBeenCalledTimes(1);
    expect(repository.getInstance(instance.id)?.status).toBe("completed");
  });

  it("dispatches a resumable waiting instance to continue it", async () => {
    const side = makeSide(declareCompleted("finished after reply"), vi.fn());
    const runner = new BackgroundRunner(makeDeps(side));

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
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
    const side = makeSide(declareCompleted("done"), vi.fn());
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

    // Each dispatched instance declares completed on its only turn but holds its slot until
    // released, so the concurrency cap is genuinely saturated mid-flight. Capturing customTools
    // and driving update_goal mirrors how a real run terminates after one prompt.
    const openBackgroundSession = vi.fn(async (options: { customTools?: HarnessTool[] }) => {
      active += 1;
      peak = Math.max(peak, active);

      const tools = options.customTools ?? [];
      const prompt = vi.fn(async () => {
        const updateGoal = tools.find((tool) => tool.name === "update_goal");
        await updateGoal?.execute("call-1", {
          status: "completed",
          goalRestated: SNAPSHOTTED_GOAL,
          evidence: "done",
          summary: "done",
        });
        await new Promise<void>((resolve) => {
          releases.push(() => {
            active -= 1;
            resolve();
          });
        });
      });

      return {
        sessionFile: "/sessions/concurrent.jsonl",
        messages: [{ role: "assistant", content: [{ type: "text", text: "done" }] }],
        prompt,
        dispose: vi.fn(),
      };
    });

    const runner = new BackgroundRunner(
      makeDeps({ openBackgroundSession, run: vi.fn() } as unknown as BackgroundSide, 10, 2),
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
      run: vi.fn(),
    } as unknown as BackgroundSide);
    const runner = new BackgroundRunner(deps);

    const instance = pendingInstance(SNAPSHOTTED_GOAL);
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
    const side = makeSide(declareCompleted("done"), vi.fn());
    const runner = new BackgroundRunner(makeDeps(side));

    const cancelled = await runner.cancel("not-in-flight");

    expect(cancelled).toBe(false);
  });
});
