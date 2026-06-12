import { StringEnum } from "@earendil-works/pi-ai";
import { type Static, Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";

export const BoundaryDecisionSchema = Type.Object({
  decision: StringEnum(["continue", "new", "resume"] as const),
  resumeSessionId: Type.Optional(Type.Number()),
});

export type BoundaryDecision = Static<typeof BoundaryDecisionSchema>;

export type Classifier = Pick<SideRunner, "classify">;

export interface SessionCandidate {
  id: number;
  summary: string;
}

export interface BoundaryInput {
  message: string;
  activeSummary: string | null;
  lastExchange: string | null;
  candidates: SessionCandidate[];
}

const SYSTEM = `You segment an ongoing conversation with a personal assistant into topical sessions.

Given the active session's rolling summary, the last exchange, a list of recently closed
sessions, and a new incoming message, decide:

- "continue": the message belongs to the active session's topic (default when in doubt,
  and always when the message is a short reaction, follow-up, or answer to the assistant).
- "new": the message clearly starts an unrelated topic.
- "resume": the message clearly picks up the topic of one of the closed sessions listed
  as candidates; set resumeSessionId to that candidate's id.

Only choose "resume" when the match is unambiguous. Never invent ids.`;

const renderInput = ({
  message,
  activeSummary,
  lastExchange,
  candidates,
}: BoundaryInput): string => {
  const sections = [
    activeSummary != null
      ? `<active-session-summary>\n${activeSummary}\n</active-session-summary>`
      : "<active-session-summary>none — no session is active</active-session-summary>",
    lastExchange != null ? `<last-exchange>\n${lastExchange}\n</last-exchange>` : null,
    candidates.length > 0
      ? `<closed-sessions>\n${candidates
          .map((candidate) => `- id ${candidate.id}: ${candidate.summary}`)
          .join("\n")}\n</closed-sessions>`
      : null,
    `<incoming-message>\n${message}\n</incoming-message>`,
  ];

  return sections.filter((section) => section != null).join("\n\n");
};

export const detectBoundary = async (
  classifier: Classifier,
  input: BoundaryInput,
  log: Logger,
): Promise<BoundaryDecision> => {
  try {
    const decision = await classifier.classify({
      system: SYSTEM,
      user: renderInput(input),
      schema: BoundaryDecisionSchema,
      tier: "classifier",
    });

    if (decision.decision === "resume") {
      const valid = input.candidates.some((candidate) => candidate.id === decision.resumeSessionId);

      if (!valid) {
        log.warn({ decision }, "boundary picked an unknown session id — continuing instead");
        return { decision: "continue" };
      }
    }

    return decision;
  } catch (error) {
    // Boundary detection is best-effort: a failure must never block the message.
    log.error({ err: error }, "boundary detection failed — continuing active session");
    return { decision: "continue" };
  }
};
