import { StringEnum } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import { buildBackgroundSystemPrompt } from "../../agent/prompts.ts";
import { lastAssistantText, type SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { PostProcessorContext } from "../api.ts";
import { NOTIFY_EVENT, type NotifyPayload, SEVERITIES } from "../notifications/payload.ts";
import type { TaskRepository } from "./repository.ts";
import type { TaskInstanceRecord } from "./schema.ts";

// Structured output of a run-start goal extraction: a single free-text goal derived
// from the task prompt. See GOAL_EXTRACTION_SYSTEM for the recommended prose structure.
export const ExtractedGoalSchema = Type.Object({
  goal: Type.String(),
});

export type ExtractedGoal = Static<typeof ExtractedGoalSchema>;

// A terminal self-declaration captured by the update_goal tool's execute handler. The loop
// reads it (snapshotted to an annotated const) after each turn: `completed` → mark complete
// with the summary as the result; `not_completable` → fail with the reason in the notice.
export type PendingDeclaration =
  | { status: "completed"; summary: string }
  | { status: "not_completable"; reason: string };

export type NotifyEmitter = (event: string, payload: NotifyPayload) => void;

export type BackgroundSide = Pick<SideRunner, "classify" | "openBackgroundSession">;

export interface ExecutorDeps {
  repository: TaskRepository;
  side: BackgroundSide;
  /** Emit a `"notify"` event so background-task notices flow through the notifications router. */
  emit: NotifyEmitter;
  /** Run the registered post-processors after a task completes (workspace persistence). */
  runPostProcessors: (context: PostProcessorContext) => Promise<void>;
  maxIterations: number;
  maxConcurrent: number;
  timezone: string | undefined;
  now: () => Date;
  log: Logger;
}

const RESPONSE_EXCERPT_CHARS = 4000;

// R15's "empty, trivial, or malformed" threshold: an extracted goal shorter than this
// (after trim) is treated as unusable — extraction returns null, nothing is persisted, and
// the next run retries. A tunable heuristic; 50 chars rules out bare "do the task"-style
// goals while comfortably admitting a one-sentence end state plus its check.
export const MIN_GOAL_LENGTH = 50;

// The instruction appended to every fresh-run opening turn telling the agent it owns its
// own terminal outcome and how to declare it via update_goal. The goal's recommended prose
// structure (end state + stated check + invariants) lives in GOAL_EXTRACTION_SYSTEM; this is
// the declaration verb. The agent is the sole judge of when its goal is met (R5/R6).
const DECLARE_INSTRUCTION = `\n\nYou are responsible for deciding when this task is done. When the goal's stated check is satisfied, finish by calling the update_goal tool with status="completed": restate the goal in your own words (goalRestated), cite concrete checkable evidence that the stated check is met (evidence — an artifact, measurement, or observable outcome, not a bare assertion), and summarize what you accomplished (summary). If the goal genuinely cannot be completed, call update_goal with status="not_completable" and a reason. update_goal cannot edit the goal text — it only records a terminal status. Work until the goal is actually met, then declare it.`;

// Formats the timezone-aware header here (this extension owns the configured timezone) and hands
// the prompt text composition to the shared prompt module.
const buildSystemPrompt = (now: Date, timezone: string | undefined): string => {
  const formatted = now.toLocaleString("en-US", {
    timeZone: timezone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const zone = timezone != null ? ` ${timezone}` : "";

  return buildBackgroundSystemPrompt({ dateHeader: `${formatted}${zone}` });
};

// Derives a free-text completion goal from a task prompt. The goal anchors what "done"
// means for the run; the recommended prose structure (end state + stated check +
// invariants) is enforced by instruction here and on the other agent-facing surfaces, not
// by a parsed schema. A vague prompt yields an empty goal, which the heuristic rejects.
const GOAL_EXTRACTION_SYSTEM = `You derive a concise completion goal for an autonomous background task from its task prompt.

The goal anchors what "done" means. Express it as:
- A measurable END STATE: the concrete outcome the task produces.
- A STATED CHECK: how the agent can verify that end state is reached — an artifact, a measurement, or another observable outcome (not a bare assertion).
- INVARIANTS: constraints on what must NOT change while reaching it (for example, "do not break existing behavior" or "do not modify files outside the project").

Rules:
- State the goal as a short paragraph or a few lines in the agent's own working terms — NOT a list of steps, and NOT meta-commentary about the task.
- Be specific and genuinely checkable; reject trivially satisfied goals like "do the task" or "work on it until finished".
- Derive ONLY from the task prompt; do not invent scope the prompt does not imply.
- If the prompt is too vague to derive a meaningful, checkable goal, return an empty goal string.`;

// The opening turn surfaces the task and the goal (when one is present), then appends the
// declare-via-update_goal instruction in both branches — even on the task-prompt basis the
// agent must know it owns its terminal outcome (S4a). Workspace context arrives via each
// extension's background-scoped context section, not folded into this prompt.
const buildOpeningPrompt = (taskPrompt: string, goal: string | null): string => {
  const body =
    goal == null ? taskPrompt : `<task>\n${taskPrompt}\n</task>\n\n<goal>\n${goal}\n</goal>`;
  return `${body}${DECLARE_INSTRUCTION}`;
};

// The per-turn completion nudge: a USER message (not a system note) that asks the agent to
// re-evaluate the goal and declare via update_goal if its check is met, while explicitly
// allowing it to keep working — threading the needle between forcing a premature declaration
// and letting a forgetful agent loop without revisiting completion (R8, S7).
const buildGoalNudge = (): string =>
  `Evaluate whether your goal is complete now. If its stated check is satisfied, call update_goal with status="completed" (restate the goal and cite concrete evidence). If it cannot be completed, call update_goal with status="not_completable" with a reason. Otherwise, keep working toward the goal.`;

// Legacy fallback for instances created before persistent background sessions: a
// paused-then-answered run with no session file is resumed by replaying the captured
// progress, the question it asked, and the user's reply into a fresh prompt.
const buildResumePrompt = (
  taskPrompt: string,
  resumeContext: string,
  question: string,
  userResponse: string,
) =>
  `You are resuming a background task that paused to ask the user a question.

<task>
${taskPrompt}
</task>

<previous-progress>
${resumeContext.slice(0, RESPONSE_EXCERPT_CHARS)}
</previous-progress>

You asked the user: ${question}

The user replied: ${userResponse}

Continue working on the task from where you left off, using the user's reply. Do not ask the same question again.`;

/**
 * Derive a completion goal from a task prompt via structured extraction. Returns the goal
 * prose when extraction succeeds and it clears the trim + minimum-length heuristic, or
 * `null` on a throw or an unusable result. A `null` result means the run proceeds on the
 * task-prompt basis and persists nothing — every later run retries until a usable goal is
 * extracted (R3/R14/R15). Lazy, marker-free migration per DES-006: a non-null goal is the
 * done signal, with no backfill pass and no give-up counter.
 */
export const extractGoal = async (
  side: Pick<SideRunner, "classify">,
  taskPrompt: string,
  log: Logger,
): Promise<string | null> => {
  try {
    const result = await side.classify({
      system: GOAL_EXTRACTION_SYSTEM,
      user: taskPrompt,
      schema: ExtractedGoalSchema,
    });
    const goal = result.goal.trim();
    return goal.length >= MIN_GOAL_LENGTH ? goal : null;
  } catch (error) {
    log.warn({ err: error }, "goal extraction failed — proceeding on task-prompt basis");
    return null;
  }
};

/** Execute one background instance through the goal-driven self-declaration loop. */
export const executeBackgroundInstance = async (
  deps: ExecutorDeps,
  instance: TaskInstanceRecord,
  signal?: AbortSignal,
): Promise<void> => {
  const { repository, side, emit, runPostProcessors, maxIterations, timezone, now, log } = deps;

  const definition =
    instance.definitionId != null ? repository.getDefinition(instance.definitionId) : null;
  const source =
    definition != null
      ? `Background task: ${definition.name}`
      : `Background task: ${instance.prompt.slice(0, 100)}`;

  const fail = (reason: string, noticeMessage: string): void => {
    repository.updateInstance(instance.id, {
      status: "failed",
      completedAt: now(),
      result: reason,
    });
    emit(NOTIFY_EVENT, {
      text: `❌ ${noticeMessage}`,
      severity: SEVERITIES.warning,
      source,
    });
    log.warn({ instanceId: instance.id, reason }, "background task failed");
  };

  // A resumable instance is a waiting run whose user reply has arrived: pick up
  // from the captured progress rather than restarting the task from scratch.
  const resuming = instance.status === "waiting" && instance.userResponse != null;

  repository.updateInstance(instance.id, {
    status: "running",
    startedAt: instance.startedAt ?? now(),
  });
  log.info({ instanceId: instance.id, resuming }, "executing background task instance");

  // Legacy instances created before persistent background sessions carry no session file. A
  // resuming legacy instance replays its captured excerpt into a fresh session; everything else
  // resumes the persistent session and prompts only the new turn.
  const legacyResume = resuming && instance.piSessionFile == null && instance.question != null;

  let session: Awaited<ReturnType<BackgroundSide["openBackgroundSession"]>> | null = null;

  // Aborting the signal cancels a run: session.abort() ends a mid-flight prompt
  // gracefully, and signal.aborted is checked between iterations. On abort the
  // loop unwinds WITHOUT writing status — the cancel initiator owns that write,
  // so the executor never clobbers a `failed`-by-cancellation row.
  const onAbort = (): void => {
    session?.abort();
  };
  signal?.addEventListener("abort", onAbort);

  try {
    const system = buildSystemPrompt(now(), timezone);

    // Goal extraction at run start: a fresh (non-resuming) run with no snapshotted goal
    // derives one from the task prompt. The derived goal is stored on the instance for this
    // run and written back to the definition ONLY when its goal is still null (never clobbering
    // a goal set by update_task or an earlier write-back in the meantime). A resumed instance
    // already has its goal from its first start, so it never re-extracts. On failure or an
    // unusable result, the run proceeds on the task-prompt basis and persists nothing — every
    // later run retries until a usable goal lands (R3/R14/R15, DES-006 lazy migration).
    let effectiveGoal = instance.goal;
    if (!resuming && effectiveGoal == null) {
      const extracted = await extractGoal(side, instance.prompt, log);
      if (extracted != null) {
        repository.updateInstance(instance.id, { goal: extracted });
        effectiveGoal = extracted;
        if (definition != null) {
          repository.setDefinitionGoalIfNull(definition.id, extracted);
        }
      }
    }

    // Workspace context (memory, projects, …) is injected by each extension's background-scoped
    // context section via pi's before_agent_start hook — no manual fold into the opening prompt.
    // notify_user is the notifications extension's background-scoped tool (bound automatically);
    // the agent decides whether to use it per the task's own guidance.
    //
    // The two per-run custom tools are closure-flag tools: their execute handler captures a
    // terminal/pause intent in a variable the loop reads after the turn (ask_user's pattern,
    // copied by update_goal). The loop trusts a flag only after the tool's own validation passes.
    let pendingQuestion: string | null = null;
    // pendingDeclaration is assigned inside the update_goal closure; TS's control-flow analysis
    // therefore keeps it at its null initializer when read here (it does not widen a
    // closure-assigned `let` back to its declared type). The post-turn dispatch casts it to the
    // declared union so the discriminated narrowing type-checks — runtime sees whatever the tool set.
    let pendingDeclaration: PendingDeclaration | null = null;

    const askUserTool = defineTool({
      name: "ask_user",
      label: "Ask User",
      description:
        "Ask the user a blocking question and pause the task until they reply. Use ONLY when you genuinely cannot proceed without their input — not for questions you can answer from available context. After calling this, stop working: the task suspends and resumes automatically once the user responds.",
      parameters: Type.Object({
        question: Type.String({ description: "The question to put to the user" }),
      }),
      execute: async (_id, params) => {
        pendingQuestion = params.question;

        return {
          content: [
            {
              type: "text",
              text: "Question delivered to the user. The task is now paused — stop working and wait; it will resume automatically with their reply.",
            },
          ],
          details: {},
        };
      },
    });

    const updateGoalTool = defineTool({
      name: "update_goal",
      label: "Update Goal",
      description:
        "Declare the terminal outcome of this background task — the ONLY way to finish it. " +
        "status='completed': restate the goal (goalRestated), cite concrete checkable evidence the stated " +
        "check is met (evidence), and summarize what you accomplished (summary). " +
        "status='not_completable': give the reason. This tool cannot edit the goal text — it only records a " +
        "terminal status.",
      parameters: Type.Object({
        // StringEnum discriminator — NOT a Type.Union of literals. pi uses StringEnum for
        // Google/Gemini API compatibility (per pi-sdk-notes), and a flat object with Optional
        // per-variant fields avoids anyOf/oneOf entirely.
        status: StringEnum(["completed", "not_completable"] as const),
        goalRestated: Type.Optional(
          Type.String({ description: "Required when status='completed'." }),
        ),
        evidence: Type.Optional(Type.String({ description: "Required when status='completed'." })),
        summary: Type.Optional(Type.String({ description: "Required when status='completed'." })),
        reason: Type.Optional(
          Type.String({ description: "Required when status='not_completable'." }),
        ),
      }),
      execute: async (_id, params) => {
        // In-tool validation: the JSON Schema alone cannot encode "evidence required iff
        // status=completed", so this validator closes that gap. On an incomplete declaration
        // we THROW (the pi-agent-core convention — "Throw on failure instead of encoding errors
        // in `content`"); the agent loop turns the thrown message into an isError tool result the
        // agent self-corrects within the SAME run, so pendingDeclaration stays null and the loop
        // never sees an invalid declaration. On a valid declaration we set the flag and return
        // success text (the loop terminates after the turn).
        if (params.status === "completed") {
          const goalRestated = params.goalRestated?.trim();
          const evidence = params.evidence?.trim();
          const summary = params.summary?.trim();
          if (goalRestated == null || goalRestated.length === 0) {
            throw new Error(
              "A completed declaration must restate the goal — 'goalRestated' is required and non-empty.",
            );
          }
          if (evidence == null || evidence.length === 0) {
            throw new Error(
              "A completed declaration must cite concrete evidence — 'evidence' is required and non-empty.",
            );
          }
          if (summary == null || summary.length === 0) {
            throw new Error(
              "A completed declaration must summarize what was accomplished — 'summary' is required and non-empty.",
            );
          }
          pendingDeclaration = { status: "completed", summary };
          return {
            content: [{ type: "text", text: "Goal marked complete — ending the run." }],
            details: {},
          };
        }

        const reason = params.reason?.trim();
        if (reason == null || reason.length === 0) {
          throw new Error(
            "A not_completable declaration must give a reason — 'reason' is required and non-empty.",
          );
        }
        pendingDeclaration = { status: "not_completable", reason };
        return {
          content: [{ type: "text", text: "Goal marked not completable — ending the run." }],
          details: {},
        };
      },
    });

    session = await side.openBackgroundSession({
      system,
      customTools: [askUserTool, updateGoalTool],
      // A legacy resume replays its excerpt into a fresh session; everything else resumes
      // the run's own persistent session file when one exists.
      sessionFile: legacyResume ? null : instance.piSessionFile,
    });

    // Record the session file immediately so a mid-run crash still leaves the resumable path.
    const transcriptPath = session.sessionFile ?? null;
    if (transcriptPath !== instance.piSessionFile) {
      repository.updateInstance(instance.id, { piSessionFile: transcriptPath });
    }

    // First turn: the resumed persistent session already holds its history, so it gets only the
    // user's reply; a legacy resume replays the captured excerpt; a fresh run gets task + goal +
    // the declare instruction.
    let prompt =
      resuming && !legacyResume
        ? (instance.userResponse as string)
        : legacyResume && instance.question != null
          ? buildResumePrompt(
              instance.prompt,
              instance.resumeContext ?? "",
              instance.question,
              instance.userResponse as string,
            )
          : buildOpeningPrompt(instance.prompt, effectiveGoal);

    for (let iteration = 1; iteration <= maxIterations; iteration += 1) {
      // Covers a cancel that landed before this iteration (between turns, or
      // before the first prompt ever runs).
      if (signal?.aborted) {
        log.info(
          { instanceId: instance.id, iteration },
          "background task aborted between iterations",
        );
        break;
      }

      await session.prompt(prompt);

      // Covers a cancel that interrupted the prompt itself (session.abort()
      // resolved it mid-flight). Bail before persisting any waiting transition.
      if (signal?.aborted) {
        log.info(
          { instanceId: instance.id, iteration },
          "background task aborted between iterations",
        );
        break;
      }

      const text = lastAssistantText(session.messages);

      // A single dispatch over the two closure flags replaces the old evaluator switch. Order:
      // ask_user pause → terminal declaration → neither (nudge). The ask_user pause path is
      // unchanged; the declaration paths are new; the "neither" path injects the nudge that
      // replaces the evaluator-note continuation (S6/S7).
      if (pendingQuestion != null) {
        repository.updateInstance(instance.id, {
          status: "waiting",
          question: pendingQuestion,
          resumeContext: text,
          userResponse: null,
          piSessionFile: transcriptPath,
        });

        emit(NOTIFY_EVENT, {
          text: `❓ Needs your input (instance ${instance.id}):\n\n${pendingQuestion}`,
          severity: SEVERITIES.urgent,
          source,
        });

        log.info({ instanceId: instance.id }, "background task paused awaiting user input");
        return;
      }

      if (pendingDeclaration != null) {
        const declaration = pendingDeclaration as PendingDeclaration;
        if (declaration.status === "completed") {
          // result is sourced from the tool's summary; the re-stated goal + evidence audit
          // lives in the session transcript (the agent emitted them through update_goal) (R10).
          repository.updateInstance(instance.id, {
            status: "completed",
            completedAt: now(),
            result: declaration.summary,
            question: null,
            resumeContext: null,
          });
          // Dispose before post-processing so memory extraction reads a flushed transcript.
          session.dispose();
          session = null;
          await runPostProcessors({ trunk: null, transcriptPath, log });
          // No programmatic success notice: the agent surfaces results via notify_user at
          // its own discretion (per the task prompt).
          log.info({ instanceId: instance.id }, "background task completed via declaration");
          return;
        }

        // not_completable → fail with the agent's reason in the notice. Reuses the fail()
        // helper so it flows through the notifications router's warning → Normal-tier mapping
        // exactly like the cap and thrown-error failures (R7).
        fail(declaration.reason, `Task cannot be completed: ${declaration.reason}`);
        return;
      }

      // Neither flag set this turn: inject the completion nudge as the next prompt. The
      // persistent session retains its own history, so the nudge is a short user message.
      prompt = buildGoalNudge();
    }

    // The iteration cap is the SOLE automatic fail-point for a run that never declares a
    // terminal outcome (R9). A single fail() call structured so the unspecced DLT-134
    // (iteration-limit escalation) can intercept this exact point to escalate to the user
    // instead of failing. Skipped on cancel: the loop `break`s out of a cancellation, and the
    // cancel initiator has already written the terminal `failed` row.
    if (!signal?.aborted) {
      fail(
        `Max iterations (${maxIterations}) reached without a terminal declaration`,
        `Task failed: reached max iterations (${maxIterations})`,
      );
    }
  } catch (error) {
    // A cancellation may surface here if abort raced with an in-flight operation;
    // the initiator owns the terminal write, so log and swallow rather than fail.
    if (signal?.aborted) {
      log.info({ instanceId: instance.id }, "background task cancelled");
    } else {
      log.error({ instanceId: instance.id, err: error }, "background task errored");
      fail(`Task failed: ${error}`, `Task failed with error: ${error}`);
    }
  } finally {
    // Guarantees the live session handle never leaks — the complete path nulls it after disposing.
    signal?.removeEventListener("abort", onAbort);
    session?.dispose();
  }
};

/**
 * Tick-driven dispatcher for background instances. In-flight executions are
 * tracked across ticks so a slow run is never dispatched twice, and a hard
 * concurrency cap bounds how many run at once — surplus pending instances are
 * left untouched and picked up on a later tick as slots free.
 */
export class BackgroundRunner {
  private readonly deps: ExecutorDeps;
  // One entry per dispatched instance, pairing its in-flight run with the
  // AbortController that cancels it. Binding them in a single map (rather than
  // two kept in lockstep) makes the controller/promise pairing structural.
  private readonly runs = new Map<
    string,
    { controller: AbortController; promise: Promise<void> }
  >();

  constructor(deps: ExecutorDeps) {
    this.deps = deps;
  }

  tick(): void {
    const dispatchable = [
      ...this.deps.repository.getResumableInstances("background"),
      ...this.deps.repository.getPendingInstances("background"),
    ];

    for (const instance of dispatchable) {
      if (this.runs.size >= this.deps.maxConcurrent) {
        this.deps.log.debug(
          {
            instanceId: instance.id,
            active: this.runs.size,
            maxConcurrent: this.deps.maxConcurrent,
          },
          "background dispatch deferred — concurrency cap reached",
        );
        break;
      }

      if (this.runs.has(instance.id)) continue;

      const controller = new AbortController();
      const promise = executeBackgroundInstance(this.deps, instance, controller.signal)
        .catch((error) =>
          this.deps.log.error(
            { instanceId: instance.id, err: error },
            "background executor crashed",
          ),
        )
        .finally(() => this.runs.delete(instance.id));

      this.runs.set(instance.id, { controller, promise });
    }
  }

  /**
   * Cancel an in-flight instance: signal its run loop to abort and await
   * the run so the caller sees it settled. Returns false when the instance is
   * not running in this process (the caller's own terminal write is then the
   * whole effect).
   */
  async cancel(instanceId: string): Promise<boolean> {
    const run = this.runs.get(instanceId);
    if (run == null) return false;

    run.controller.abort();
    // `run.promise` already swallows its own errors via the `.catch` in `tick`,
    // so awaiting it never throws — it just waits for the run to unwind.
    await run.promise;
    return true;
  }

  /** Await in-flight executions (tests and shutdown). */
  async drain(): Promise<void> {
    await Promise.allSettled([...this.runs.values()].map((r) => r.promise));
  }
}
