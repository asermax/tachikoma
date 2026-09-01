import {
  buildSessionContext,
  convertToLlm,
  type ExtensionAPI,
  type Skill,
  serializeConversation,
} from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";
import type { SideRunner } from "../../agent/side-run.ts";
import { readSkillBody, readSkillFile } from "../../agent/skill-body.ts";
import type { Logger } from "../../log.ts";

export type SkillClassifier = Pick<SideRunner, "classify">;

export interface SkillSuggestionDeps {
  classifier: SkillClassifier;
  isForking: () => boolean;
  status: (text: string) => void;
  log: Logger;
  /**
   * Reads a matched skill's SKILL.md content; defaults to the shared synchronous reader
   * (`readSkillFile`). Injectable for tests.
   */
  readSkill?: (filePath: string) => string;
  /**
   * Subscribe to a topic-change signal that resets per-branch injection state so the new branch
   * re-evaluates from scratch. Wired (main scope only) to the boundary's `session:topic-changed` event
   * by the factory; omitted for background sessions, which have no topic shifts. The factory is
   * re-invoked when the trunk reopens (≈once per day under the daily-trunk model), adding a fresh
   * handler to the process-scoped bus each time; a prior trunk's handler goes dormant once its
   * injection set is empty, so the slow accumulation is harmless.
   */
  onTopicChanged?: (handler: () => void) => void;
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

// Newly loaded skills — their full content is injected up front behind this authority framing.
const PREFACE_FULL =
  "The following skills have been proactively loaded because they match the current task — their content is already here, so no /skill load is needed. These skills define the correct process for this kind of request: follow their instructions and workflows rather than improvising an alternative approach. If a skill defines a workflow, use it.";

// Skills loaded earlier that are relevant again — their content is already in context, so we only
// re-anchor the agent to it rather than paying the tokens to re-inject the full body.
const PREFACE_REMINDER =
  "A skill loaded earlier in this conversation is relevant again to the current task. Its full instructions are already in context — re-apply them (and start or resume its workflow if one is defined) rather than improvising an alternative approach.";

// Full body of a newly matched skill, wrapped with a per-skill adherence nudge so the instruction to
// follow the skill's process lands directly under the content it applies to.
const renderFull = (skill: Skill, body: string): string =>
  `<injected-skill name="${skill.name}">\n${body}\n\n→ Follow the instructions above for this task. If a workflow is defined, start it.\n</injected-skill>`;

// Lightweight re-anchor for a skill already injected this branch: no body, just a nudge to reuse the
// instructions already in context (and start its workflow if one applies).
const renderReminder = (skill: Skill): string =>
  `<skill-reminder name="${skill.name}">\nRelevant to this task again. Its full instructions are already in context above — follow them rather than improvising, and start its workflow (start_workflow) if one applies.\n</skill-reminder>`;

/**
 * Proactive skill loading: on each genuine top-level turn, a conversation-aware classifier picks
 * which loaded skills are relevant to the latest message. A skill whose full content is not yet on
 * the active path is injected directly as a hidden, persisted message — giving the model the
 * instructions with no separate /skill load round-trip, covering the gap where pi's progressive
 * disclosure leaves loading to the model, which "does not always do this". A skill already injected
 * (its content still in context) is re-anchored with a lightweight reminder rather than re-injected
 * in full. The pass is best-effort and never blocks the response: it is skipped inside forks
 * (`isForking`), when no skills are eligible, and on any classify failure. A skill whose content
 * cannot be read (or is empty) is skipped without aborting the rest.
 */
export const registerSkillSuggestion = (pi: ExtensionAPI, deps: SkillSuggestionDeps): void => {
  const { classifier, isForking, status, log } = deps;
  const readSkill = deps.readSkill ?? readSkillFile;

  // Skills whose full content has been injected this branch and is presumed still on the active
  // transcript path. A selected member yields a lightweight reminder (its content is already in
  // context); a selected non-member is injected in full and added here. Per-session closure (the
  // factory is recreated for each agent session).
  //
  // The daily trunk is one pi session for the whole day, so injected content can leave the active
  // path without the skill being re-evaluated. Two events clear the set so the skill re-evaluates
  // from scratch: a genuine topic shift (boundary's `session:topic-changed` — auto-shift, `/new`, or
  // an earlier-branch jump, which moves content off the active path) and mid-branch compaction (pi's
  // `session_compact`, which summarizes older entries — including a previously injected skill's — out
  // of the active context). Without these resets a skill would stay "injected" after its content left.
  const injected = new Set<string>();

  // Both reset triggers (below) clear the same set for the same reason — injected content may have
  // left the active path — so they share one helper. Rationale for each trigger is in the comment
  // on `injected` above.
  const resetInjection = (reason: string): void => {
    if (injected.size === 0) return;
    injected.clear();
    log.debug(`${reason} — clearing proactive-skill injection state for re-evaluation`);
  };

  deps.onTopicChanged?.(() => resetInjection("topic changed"));
  pi.on("session_compact", () => resetInjection("session compacted"));

  pi.on("before_agent_start", async (event, ctx): Promise<SkillSuggestionResult | undefined> => {
    // A non-bare fork binds every pi factory, so this handler also fires inside the memory/context
    // post-processing forks; skip there so the classifier runs only on genuine top-level turns.
    if (isForking()) return undefined;

    const invocable = (event.systemPromptOptions.skills ?? []).filter(
      (skill) => !skill.disableModelInvocation,
    );
    if (invocable.length === 0) return undefined;

    try {
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

      // The classifier sees every invocable skill each turn (not just not-yet-injected ones) so an
      // already-injected skill that's relevant again can be re-anchored with a reminder rather than
      // silently dropped.
      const selection = await classifyWithDeadline(
        (signal) =>
          classifier.classify({
            system: `${SYSTEM}\n\n<available-skills>\n${renderCatalog(invocable)}\n</available-skills>`,
            user: renderInput(conversation, event.prompt),
            schema: SkillSelectionSchema,
            tier: "classifier",
            signal,
          }),
        SKILL_CLASSIFY_TIMEOUT_MS,
      );

      // Route each selected skill by whether its full content is already on the active path. A member
      // of `injected` gets a lightweight reminder (re-anchor, no body); a non-member is injected in
      // full and recorded. The set is cleared on compaction/topic-change, so membership reliably
      // means "still in context". The per-skill fail-soft body read (`skill-body.ts`) means one
      // unreadable (or empty) file skips only that skill — and skipped skills are not added to
      // `injected`, so they retry next turn.
      const full: { name: string; section: string }[] = [];
      const reminders: { name: string; section: string }[] = [];
      // Dedupe the classifier's selection up front (it may repeat a name) before routing each name.
      for (const name of new Set(selection.skills)) {
        const skill = invocable.find((candidate) => candidate.name === name);
        if (skill == null) continue; // classifier invented a name not in the catalog
        if (injected.has(skill.name)) {
          reminders.push({ name: skill.name, section: renderReminder(skill) });
          continue;
        }
        const body = readSkillBody(skill, readSkill, log, "proactive");
        if (body == null) continue;

        injected.add(skill.name);
        full.push({ name: skill.name, section: renderFull(skill, body) });
      }

      log.debug(
        {
          invocable: invocable.length,
          selected: selection.skills.length,
          full: full.length,
          reminders: reminders.length,
        },
        "proactive skill classify completed",
      );

      if (full.length === 0 && reminders.length === 0) return undefined;

      log.info(
        { full: full.map((s) => s.name), reminders: reminders.map((s) => s.name) },
        "injected proactive skill content",
      );

      const parts: string[] = [];
      if (full.length > 0) parts.push(PREFACE_FULL, ...full.map((s) => s.section));
      if (reminders.length > 0) parts.push(PREFACE_REMINDER, ...reminders.map((s) => s.section));

      return {
        message: {
          customType: "skill-content",
          content: parts.join("\n\n"),
          display: false,
        },
      };
    } catch (error) {
      log.warn({ err: error }, "proactive skill selection failed — skipping injection");
      return undefined;
    }
  });
};
