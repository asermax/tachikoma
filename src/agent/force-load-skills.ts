import { readFileSync } from "node:fs";

import {
  type ExtensionAPI,
  type ExtensionFactory,
  type Skill,
  stripFrontmatter,
} from "@earendil-works/pi-coding-agent";

import type { Logger } from "../log.ts";

export interface ForceLoadSkillsDeps {
  /** Skill names to force-load; each is resolved against the session's loader-discovered catalog. */
  names: readonly string[];
  log: Logger;
  /** Reads a skill's SKILL.md content; defaults to `readFileSync` (utf-8). Injectable for tests. */
  readSkill?: (filePath: string) => string;
}

type ForceLoadResult = { message: { customType: string; content: string; display: false } };

// Grounds the run in the named skills up front: their full bodies are already in context, so the
// model must follow them rather than improvise. Deliberately free of the proactive loader's
// workflow phrasing ("if a workflow is defined, start it") — a force-loaded skill may describe
// runtime machinery the calling run cannot use, so the framing stays about conventions only.
const PREFACE =
  "The following skills have been force-loaded for this run because they define the conventions this task must follow — their content is already here, so no separate load is needed. Treat them as authoritative for their subject: follow their instructions rather than improvising an alternative approach.";

// Full body of a force-loaded skill. Structural precedent: the skills extension's proactive
// injection (`suggest.ts` renderFull) and pi's own `/skill` expansion (`_expandSkillCommand`),
// both of which strip the frontmatter (catalog metadata, not instructions) before rendering. The
// render tokens are deliberately NOT shared with `suggest.ts`: its framing carries workflow-start
// nudges this path must not emit. Reconcile trigger, like `BUILTIN_TOOL_NAMES`: if pi's
// `_expandSkillCommand` shape changes, both renderers change.
const renderSkill = (skill: Skill, body: string): string =>
  `<injected-skill name="${skill.name}">\n${body}\n</injected-skill>`;

/**
 * An inline extension factory that force-loads skills by name: on the session's agent starts, it
 * resolves each name against pi's own skill catalog (`event.systemPromptOptions.skills` — the
 * loader-discovered skills, independent of whether the catalog renders into the system prompt)
 * and injects every resolved body as one hidden `skill-content` message. The loader-side
 * structural precedent is {@link provideContext}'s hidden-message mode (same envelope, same
 * once-per-session latch, and the same choice of `before_agent_start` over `session_start`,
 * which does not fire ahead of the first agent start); the catalog-sourced rendering precedent
 * is the skills extension's `suggest.ts`.
 *
 * A successfully injected skill is never re-injected in the session (per-name latch); a skill
 * that could not be loaded is skipped with a warning and stays eligible for a later turn. The
 * per-skill reads are fail-soft; any throw from outside them (e.g. a malformed catalog shape)
 * is swallowed by pi's extension runner, which isolates `before_agent_start` handlers — the
 * run proceeds ungrounded either way. Unlike the proactive loader there is no
 * compaction/topic-change re-evaluation, so this is intended for single-prompt isolated runs
 * (`SideRunner.run`) — not persistent multi-turn sessions.
 *
 * Opened through `AgentManager.open({ skillPaths, forceLoadSkills })`; the skill source itself is
 * the caller's concern, typically `skillPaths` pointing the loader at the directory that owns
 * the named skills.
 */
export const forceLoadSkillsFactory = ({
  names,
  log,
  readSkill = (filePath) => readFileSync(filePath, "utf-8"),
}: ForceLoadSkillsDeps): ExtensionFactory => {
  // Names whose full content has been injected this session. A name that failed to load is NOT
  // added, so it retries on a later agent start (a single-prompt run simply never gets one).
  const injected = new Set<string>();

  return (pi: ExtensionAPI) => {
    pi.on("before_agent_start", async (event): Promise<ForceLoadResult | undefined> => {
      const catalog = event.systemPromptOptions.skills ?? [];
      const sections: string[] = [];

      for (const name of names) {
        if (injected.has(name)) continue;

        const skill = catalog.find((candidate) => candidate.name === name);
        if (skill == null) {
          // Not a failure: the catalog is the caller's skill source, so a missing name means the
          // source moved or the skill was renamed. Warn (visible, actionable) and carry on.
          log.warn({ skill: name }, "force-loaded skill not in the session catalog — skipping");
          continue;
        }

        try {
          // Strip the frontmatter and trim, matching pi's `/skill` expansion: the frontmatter is
          // catalog metadata (name/description), not instructions.
          const body = stripFrontmatter(readSkill(skill.filePath)).trim();
          if (body === "") {
            log.debug({ skill: name }, "force-loaded skill content is empty — skipping injection");
            continue;
          }

          injected.add(name);
          sections.push(renderSkill(skill, body));
        } catch (error) {
          log.warn(
            { err: error, skill: name },
            "failed to read force-loaded skill content — skipping injection",
          );
        }
      }

      if (sections.length === 0) return undefined;

      log.debug({ skills: [...injected] }, "injected force-loaded skill content");
      return {
        message: {
          customType: "skill-content",
          content: [PREFACE, ...sections].join("\n\n"),
          display: false,
        },
      };
    });
  };
};
