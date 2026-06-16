import type { Logger } from "../../log.ts";
import type { AgentApi } from "../api.ts";

/**
 * Shadow-fork topic-shift classifier. On each idle user message it forks the live branch
 * into a throwaway headless session and asks the conversation itself whether the message continues the
 * current topic. The live session is never mutated (R6). Detection fails open (R10): any error or
 * unparseable output is treated as "continue" so a classifier failure never blocks the message.
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
}

export type ShiftDecision = "continue" | "shift";

const buildClassifierPrompt = (message: string): string =>
  [
    "I am deciding whether to continue this conversation with a new user message, or whether that",
    "message starts a separate unrelated topic.",
    "Please answer from the perspective of this current conversation.",
    "Return continue for follow-ups, clarifications, answers to you, corrections, short replies,",
    "reactions, or ambiguous messages.",
    "Return shift only when the message clearly starts an unrelated task or topic.",
    'Return exactly one JSON object and no other text: {"decision":"continue"|"shift","reason":"short reason"}.',
    "",
    "<candidate_user_message>",
    message,
    "</candidate_user_message>",
  ].join("\n");

/**
 * Tolerant JSON parse: extract the first balanced object and read `decision`. Anything that is not an
 * explicit "shift" — including unparseable output — degrades to "continue" (fail-open).
 */
const parseDecision = (text: string): ShiftDecision => {
  const match = text.match(/\{[\s\S]*\}/);

  if (match == null) return "continue";

  const parsed = JSON.parse(match[0]) as { decision?: unknown };

  return parsed.decision === "shift" ? "shift" : "continue";
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

    return parseDecision(await fork.prompt(buildClassifierPrompt(input.message)));
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
