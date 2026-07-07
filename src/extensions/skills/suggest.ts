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
  /**
   * Subscribe to a topic-change signal that resets per-branch injection state so the new branch
   * re-evaluates from scratch. Wired (main scope only) to the boundary's `session:topic-changed` event
   * by the factory; omitted for background sessions, which have no topic shifts. Returns an unsubscribe
   * (the main-session factory runs once per process, so the single subscription lives for the trunk).
   */
  onTopicChanged?: (handler: () => void) => () => void;
}

export const SkillSelectionSchema = Type.Object({
  // `default: []` so a classifier that signals "no skills" by omitting the key (or returning `{}`)
  // is treated as an empty selection rather than a parse failure — `parseWithSchema` applies the
  // default before asserting, so the property is always present and stays typed as `string[]`.
  skills: Type.Array(Type.String(), {
    description: "Names of available skills (from the catalog) worth loading; empty if none.",
    default: [],
  }),
});

export type SkillSelection = Static<typeof SkillSelectionSchema>;

// Bail out of a slow classify rather than stalling the user-visible response. The deadline aborts
// the underlying request, so this is a hard ceiling — a capped, temperature-0 classify normally
// settles in well under this, and a hung call is cancelled here rather than left racing.
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

// Run classify against a deadline that aborts the underlying request — not a Promise.race that
// rejects while leaving the HTTP call in flight. Aborting cancels the provider stream (no dangling
// request, no late settlement to leak), and the timer is cleared once classify settles. Any
// rejection (timeout abort or otherwise) degrades to no-injection via the handler's try/catch.
const classifyWithDeadline = async <T>(
  classify: (signal: AbortSignal) => Promise<T>,
  ms: number,
): Promise<T> => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await classify(controller.signal);
  } finally {
    clearTimeout(timer);
  }
};

type SkillSuggestionResult = { message: { customType: string; content: string; display: false } };

const PREFACE =
  "The following skills have been proactively loaded because they match the current task — their content is already here, so no /skill load is needed. These skills define the correct process for this kind of request: follow their instructions and workflows rather than improvising an alternative approach. If a skill defines a workflow, use it.";

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

  // Skills injected this branch — not re-injected while the branch is live, since the message persists
  // on the active transcript path. Per-session closure (the factory is recreated for each agent
  // session). The daily trunk is one session for the whole day, so without a reset a skill injected on
  // an earlier (later-collapsed) branch would stay "injected" after its content left the active path.
  // The boundary emits `session:topic-changed` on a genuine topic shift (auto-shift, `/new`, or jumping
  // to an earlier branch); subscribing clears the set so the new branch re-evaluates from scratch.
  const injected = new Set<string>();

  deps.onTopicChanged?.(() => {
    if (injected.size === 0) return;
    injected.clear();
    log.debug("topic changed — clearing proactive-skill injection state for re-evaluation");
  });

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

      const selection = await classifyWithDeadline(
        (signal) =>
          classifier.classify({
            system: `${SYSTEM}\n\n<available-skills>\n${renderCatalog(candidates)}\n</available-skills>`,
            user: renderInput(conversation, event.prompt),
            schema: SkillSelectionSchema,
            tier: "classifier",
            signal,
          }),
        SKILL_CLASSIFY_TIMEOUT_MS,
      );

      const matched = selection.skills
        .map((name) => candidates.find((candidate) => candidate.name === name))
        .filter((skill): skill is Skill => skill != null && !injected.has(skill.name));

      log.debug(
        {
          candidates: candidates.length,
          selected: selection.skills.length,
          matched: matched.length,
        },
        "proactive skill classify completed",
      );

      if (matched.length === 0) return undefined;

      // Read each matched skill's full content and inject it directly, so the model has the
      // instructions without a separate /skill load round-trip. A per-skill try/catch means one
      // unreadable (or empty) file skips only that skill rather than aborting the whole injection;
      // skipped skills are not added to `injected`, so a transient failure can retry next turn.
      const sections: string[] = [];
      const injectedNames: string[] = [];
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
          injectedNames.push(skill.name);
          // Each section ends with a per-skill adherence nudge so the instruction to follow the
          // skill's process lands directly under the content it applies to (matters most when several
          // skills are injected together), reinforcing the preface's authority framing.
          sections.push(
            `<injected-skill name="${skill.name}">\n${body}\n\n→ Follow the instructions above for this task. If a workflow is defined, start it.\n</injected-skill>`,
          );
        } catch (error) {
          log.warn(
            { err: error, skill: skill.name },
            "failed to read skill content — skipping injection",
          );
        }
      }

      if (sections.length === 0) return undefined;

      log.info({ skills: injectedNames }, "injected proactive skill content");

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
