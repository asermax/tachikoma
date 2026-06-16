import {
  buildSessionContext,
  convertToLlm,
  type ExtensionAPI,
  type Skill,
  serializeConversation,
} from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";

export type SkillClassifier = Pick<SideRunner, "classify">;

export interface SkillSuggestionDeps {
  classifier: SkillClassifier;
  isForking: () => boolean;
  status: (text: string) => void;
  log: Logger;
}

export const SkillSelectionSchema = Type.Object({
  skills: Type.Array(Type.String(), {
    description: "Names of available skills (from the catalog) worth loading; empty if none.",
  }),
});

export type SkillSelection = Static<typeof SkillSelectionSchema>;

// Bail out of a slow classify rather than stalling the user-visible response.
export const SKILL_CLASSIFY_TIMEOUT_MS = 10_000;

// Recent conversation messages serialized as context for the selection — bounded so a long
// conversation does not bloat the classifier call.
const RECENT_MESSAGES = 16;

const SYSTEM = `You decide which of the assistant's available skills are worth loading to help with the latest user message.

You are given the recent conversation, the latest user message, and a catalog of available skills (name and description). Select only skills whose full instructions would clearly help the assistant respond to the latest message. Prefer an empty selection when no skill is clearly relevant. Never invent skill names — choose only from the catalog.`;

const renderCatalog = (skills: Skill[]): string =>
  skills.map((skill) => `- ${skill.name}: ${skill.description}`).join("\n");

const renderInput = (conversation: string, latestMessage: string): string =>
  [
    conversation.length > 0
      ? `<conversation>\n${conversation}\n</conversation>`
      : "<conversation>none — this is the first message</conversation>",
    `<latest-user-message>\n${latestMessage}\n</latest-user-message>`,
  ].join("\n\n");

// Race a classify against a timeout that rejects, so a slow/hung call degrades to no-injection via
// the handler's try/catch. The classify promise gets its own catch so a post-timeout settlement
// never surfaces as an unhandled rejection, and the timer is cleared once the race resolves.
const classifyWithTimeout = async <T>(work: Promise<T>, ms: number): Promise<T> => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  work.catch(() => {});

  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("skill classify timed out")), ms);
  });

  try {
    return await Promise.race([work, timeout]);
  } finally {
    if (timer != null) clearTimeout(timer);
  }
};

type SkillSuggestionResult = { message: { customType: string; content: string; display: false } };

const PREFACE =
  "These skills look relevant to the current request. Load each one before responding with its /skill command, then follow its instructions:";

/**
 * Proactive skill loading: on each genuine top-level turn, a conversation-aware classifier picks
 * which loaded skills are relevant to the latest message and injects a hidden message recommending
 * the agent load them — covering the gap where pi's progressive disclosure leaves loading to the
 * model, which "does not always do this". The pass is best-effort and never blocks the response:
 * it is skipped inside forks (`isForking`), when no skills are eligible, and on any classify failure.
 */
export const registerSkillSuggestion = (pi: ExtensionAPI, deps: SkillSuggestionDeps): void => {
  const { classifier, isForking, status, log } = deps;

  // Skills injected this session — never re-injected, since the message persists in the transcript.
  // Per-session: the factory (and this closure) is recreated for each agent session.
  const injected = new Set<string>();

  pi.on("before_agent_start", async (event, ctx): Promise<SkillSuggestionResult | undefined> => {
    // A non-bare fork binds every pi factory, so this handler also fires inside the memory/context
    // post-processing forks; skip there so the classifier runs only on genuine top-level turns.
    if (isForking()) return undefined;

    try {
      const candidates = (event.systemPromptOptions.skills ?? []).filter(
        (skill) => !skill.disableModelInvocation && !injected.has(skill.name),
      );

      if (candidates.length === 0) return undefined;

      status("Checking for relevant skills…");

      // The standalone resolver mirrors the SessionManager instance method
      // (buildSessionContext(getEntries(), leafId)); ReadonlySessionManager exposes both inputs.
      const resolved = buildSessionContext(
        ctx.sessionManager.getEntries(),
        ctx.sessionManager.getLeafId(),
      );
      const conversation = serializeConversation(
        convertToLlm(resolved.messages.slice(-RECENT_MESSAGES)),
      );

      const selection = await classifyWithTimeout(
        classifier.classify({
          system: `${SYSTEM}\n\n<available-skills>\n${renderCatalog(candidates)}\n</available-skills>`,
          user: renderInput(conversation, event.prompt),
          schema: SkillSelectionSchema,
          tier: "classifier",
        }),
        SKILL_CLASSIFY_TIMEOUT_MS,
      );

      const matched = selection.skills
        .map((name) => candidates.find((candidate) => candidate.name === name))
        .filter((skill): skill is Skill => skill != null && !injected.has(skill.name));

      if (matched.length === 0) return undefined;

      for (const skill of matched) injected.add(skill.name);

      log.debug({ skills: matched.length }, "recommending proactive skills");

      const lines = matched.map((skill) => `- /skill:${skill.name} — ${skill.description}`);

      return {
        message: {
          customType: "skill-recommendation",
          content: `${PREFACE}\n${lines.join("\n")}`,
          display: false,
        },
      };
    } catch (error) {
      log.warn({ err: error }, "proactive skill selection failed — skipping injection");
      return undefined;
    }
  });
};
