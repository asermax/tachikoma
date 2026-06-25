import type { Logger } from "../../log.ts";
import type { AgentApi } from "../api.ts";

/**
 * Shadow-fork conversation-boundary classifier. On each idle user message it forks the live branch
 * into a throwaway headless session and asks the conversation itself how the message should be handled:
 * continue the current topic, shift to a new one, or (DLT-181) park/return a short, unrelated side task against a checkpoint.
 * The live session is never mutated (R6). Detection fails open (R11): any error or unparseable output is
 * treated as "continue" so a classifier failure never blocks the message.
 */

export interface ShiftDeps {
  shadowFork: AgentApi["shadowFork"];
  /** The live system prompt to inherit in the shadow, so it classifies as the same assistant. */
  getSystemPrompt: () => string;
  log: Logger;
}

export interface ShiftInput {
  sessionFile: string;
  /**
   * Whether the current branch has at least one completed assistant turn since its base. The
   * empty-branch guard skips classification (and the fork) for a branch too small to summarize, so
   * two shifts in immediate succession cannot collapse an empty branch.
   */
  currentBranchHasAssistantTurn: boolean;
  message: string;
  /**
   * Whether a checkpoint is currently active (a side task is in flight). The classifier runs on a
   * detached shadowFork and cannot read the live trunk's state, so the middleware injects this. It
   * gates which checkpoint decision is even valid: `set-checkpoint` only when none is active,
   * `summarize-to-checkpoint` only when one is (S3/KD8).
   */
  checkpointActive: boolean;
}

/**
 * The classifier's decision. `continue`/`shift` are the original topic-boundary results; the two
 * checkpoint results (DLT-181) drive the side-task auto-flow: `set-checkpoint` parks the main line
 * when a short, unrelated side task begins (e.g. a quick expense log mid-workflow), and
 * `summarize-to-checkpoint` folds that side task back when the conversation returns to the main line.
 */
export type ShiftDecision = "continue" | "shift" | "set-checkpoint" | "summarize-to-checkpoint";

/**
 * Build the classifier prompt. The checkpoint section is conditional on `checkpointActive` so only the
 * valid checkpoint decision for the current state is offered (the middleware gates defensively too).
 * Posture is deliberately conservative (KD8): a checkpoint decision is emitted only when it is clearly
 * the right call; ambiguity falls back to `continue` (or `shift` for a genuinely new topic).
 */
const buildClassifierPrompt = (message: string, checkpointActive: boolean): string =>
  [
    "I am deciding how this conversation should proceed with a new user message.",
    "Answer from the perspective of this current conversation and choose exactly one decision.",
    "",
    "Decisions:",
    '- "continue": the message is a follow-up, clarification, answer to me, correction, short reply,',
    "  reaction, or otherwise continues the current thread. This is the default when uncertain.",
    '- "shift": the message clearly starts a new, substantive topic or task that should become the',
    "  main line and begin fresh (the current topic is effectively done or being set aside).",
    ...(checkpointActive
      ? [
          '- "summarize-to-checkpoint": a checkpoint is currently active (a side task is in flight). The',
          "  main line is the conversation that came before this side task. Choose this when the message",
          "  clearly returns to that main line — either it explicitly references going back to or resuming",
          '  what was discussed before the side task (e.g. "going back to the report"), or its topic clearly',
          "  matches the main-line topic, signalling the side task is set aside and the original",
          "  conversation resumes. This is a RETURN to an existing conversation, not a new topic, so do",
          '  not choose "shift" for it. Keep choosing "continue" for further side-task turns, and choose',
          '  "shift" only for a genuinely new topic that is neither the side task nor the main line. When',
          '  unsure whether the message returns to the main line, prefer "continue".',
        ]
      : [
          '- "set-checkpoint": the message starts a short, self-contained side topic that is unrelated to',
          "  the current workflow or conversation but not important enough to warrant a full topic shift",
          "  — a quick side request made while the user is mid-task (e.g. logging an expense, a quick",
          "  lookup, a brief question). The main line stays parked and resumes once the side task is done.",
          "  Use it ONLY when no checkpoint is active, the current line is worth resuming, and the side",
          '  task is clearly short (about 1-2 turns). Prefer "continue" for follow-ups to the current task',
          '  and "shift" for a substantive new topic.',
        ]),
    "",
    "Be conservative about parking a side conversation: only choose a checkpoint decision when it is",
    'clearly the right call; when unsure, prefer "continue" (or "shift" for a clearly new topic).',
    'Return exactly one JSON object and no other text: {"decision":"continue"|"shift"|"set-checkpoint"|"summarize-to-checkpoint","reason":"short reason"}.',
    "",
    "<candidate_user_message>",
    message,
    "</candidate_user_message>",
  ].join("\n");

const isDecision = (value: unknown): value is ShiftDecision =>
  value === "continue" ||
  value === "shift" ||
  value === "set-checkpoint" ||
  value === "summarize-to-checkpoint";

/**
 * Tolerant JSON parse: extract the first balanced object and read `decision`. Anything that is not a
 * recognized decision value — including unparseable output — degrades to "continue" (fail-open, R11).
 */
const parseDecision = (text: string, log: Logger): ShiftDecision => {
  const match = text.match(/\{[\s\S]*\}/);

  if (match == null) return "continue";

  try {
    const decision = (JSON.parse(match[0]) as { decision?: unknown }).decision;
    return isDecision(decision) ? decision : "continue";
  } catch (error) {
    log.debug(
      { err: error },
      "topic-shift classifier output unparseable — continuing current topic",
    );
    return "continue";
  }
};

export const classifyShift = async (deps: ShiftDeps, input: ShiftInput): Promise<ShiftDecision> => {
  if (!input.currentBranchHasAssistantTurn) {
    return "continue";
  }

  let fork: Awaited<ReturnType<AgentApi["shadowFork"]>> | null = null;

  try {
    fork = await deps.shadowFork(input.sessionFile, {
      systemPrompt: deps.getSystemPrompt(),
      tier: "classifier",
    });

    return parseDecision(
      await fork.prompt(buildClassifierPrompt(input.message, input.checkpointActive)),
      deps.log,
    );
  } catch (error) {
    deps.log.error({ err: error }, "topic-shift classification failed — continuing current topic");
    return "continue";
  } finally {
    if (fork != null) {
      await fork.dispose().catch((error) => {
        deps.log.warn({ err: error }, "shadow-fork dispose failed");
      });
    }
  }
};
