import { readFileSync } from "node:fs";
import {
  buildSessionContext,
  convertToLlm,
  type ExtensionAPI,
  type Skill,
  serializeConversation,
  stripFrontmatter,
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
  /** Reads a matched skill's SKILL.md content; defaults to `readFileSync` (utf-8). Injectable for tests. */
  readSkill?: (filePath: string) => string;
}

export const SkillSelectionSchema = Type.Object({
  skills: Type.Array(Type.String(), {
    description: "Names of available skills (from the catalog) worth loading; empty if none.",
  }),
});

export type SkillSelection = Static<typeof SkillSelectionSchema>;

// Bail out of a slow classify rather than stalling the user-visible response.
export const SKILL_CLASSIFY_TIMEOUT_MS = 30_000;

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
  "The following skill content has been injected for this session — the instructions below are available immediately, so no /skill command is needed. Follow each skill's instructions where relevant:";

/**
 * Proactive skill loading: on each genuine top-level turn, a conversation-aware classifier picks
 * which loaded skills are relevant to the latest message and injects each matched skill's full
 * SKILL.md content directly as a hidden, persisted message — giving the model the instructions
 * with no separate /skill load round-trip, covering the gap where pi's progressive disclosure
 * leaves loading to the model, which "does not always do this". The pass is best-effort and never
 * blocks the response: it is skipped inside forks (`isForking`), when no skills are eligible, and
 * on any classify failure. A skill whose content cannot be read (or is empty) is skipped without
 * aborting the rest.
 */
export const registerSkillSuggestion = (pi: ExtensionAPI, deps: SkillSuggestionDeps): void => {
  const { classifier, isForking, status, log } = deps;
  const readSkill = deps.readSkill ?? ((filePath: string) => readFileSync(filePath, "utf-8"));

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

      // Read each matched skill's full content and inject it directly, so the model has the
      // instructions without a separate /skill load round-trip. A per-skill try/catch means one
      // unreadable (or empty) file skips only that skill rather than aborting the whole injection;
      // skipped skills are not added to `injected`, so a transient failure can retry next turn.
      const sections: string[] = [];
      for (const skill of matched) {
        try {
          // Strip the YAML frontmatter and trim — matching how pi renders a skill loaded via
          // `/skill` (its `_expandSkillCommand` calls `stripFrontmatter(content).trim()`): the
          // frontmatter is metadata (name/description) already surfaced in the catalog, not
          // instructions, so injecting it only wastes tokens.
          const body = stripFrontmatter(readSkill(skill.filePath)).trim();
          if (body === "") {
            log.debug(
              { skill: skill.name },
              "proactive skill content is empty — skipping injection",
            );
            continue;
          }
          injected.add(skill.name);
          sections.push(`<injected-skill name="${skill.name}">\n${body}\n</injected-skill>`);
        } catch (error) {
          log.warn(
            { err: error, skill: skill.name },
            "failed to read skill content — skipping injection",
          );
        }
      }

      if (sections.length === 0) return undefined;

      log.debug({ skills: sections.length }, "injecting proactive skill content");

      return {
        message: {
          customType: "skill-content",
          content: `${PREFACE}\n\n${sections.join("\n\n")}`,
          display: false,
        },
      };
    } catch (error) {
      log.warn({ err: error }, "proactive skill selection failed — skipping injection");
      return undefined;
    }
  });
};
