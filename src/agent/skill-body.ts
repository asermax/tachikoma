import { readFileSync } from "node:fs";

import { type Skill, stripFrontmatter } from "@earendil-works/pi-coding-agent";

import type { Logger } from "../log.ts";

/** Default SKILL.md reader: synchronous utf-8 (both injectors run off the hot path). */
export const readSkillFile = (filePath: string): string => readFileSync(filePath, "utf-8");

/**
 * A skill's instruction body: the file with its frontmatter stripped and trimmed, matching pi's
 * `/skill` expansion (`_expandSkillCommand` calls `stripFrontmatter(content).trim()`) — the
 * frontmatter is catalog metadata (name/description), not instructions, so injecting it only
 * wastes tokens. Shared by the two hidden-`skill-content` injectors — the skills extension's
 * proactive loader (`suggest.ts`) and the agent layer's force-loader (`force-load-skills.ts`) —
 * while each keeps its own framing and render tokens. `label` names the caller in the log lines.
 *
 * Fail-soft per skill: an unreadable file warns and an empty body debug-logs, both returning
 * null so the caller skips that skill without aborting the rest of its selection.
 */
export const readSkillBody = (
  skill: Skill,
  readSkill: (filePath: string) => string,
  log: Logger,
  label: string,
): string | null => {
  try {
    const body = stripFrontmatter(readSkill(skill.filePath)).trim();
    if (body === "") {
      log.debug({ skill: skill.name }, `${label} skill content is empty — skipping injection`);
      return null;
    }
    return body;
  } catch (error) {
    log.warn(
      { err: error, skill: skill.name },
      `failed to read ${label} skill content — skipping injection`,
    );
    return null;
  }
};
