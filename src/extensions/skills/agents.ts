import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { parseFrontmatter } from "@earendil-works/pi-coding-agent";

import type { Logger } from "../../log.ts";

export interface SkillAgent {
  /** Namespaced as "<skill>/<agent>" to prevent collisions across skills. */
  name: string;
  description: string;
  tools: string[] | null;
  /** "provider/model-id[:thinkingLevel]" reference; null runs on the side-runner's default tier. */
  model: string | null;
  systemPrompt: string;
  skill: string;
}

const listDirectories = (path: string): string[] => {
  try {
    return readdirSync(path, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch {
    return [];
  }
};

const listMarkdownFiles = (path: string): string[] => {
  try {
    return readdirSync(path, { withFileTypes: true })
      .filter((entry) => (entry.isFile() || entry.isSymbolicLink()) && entry.name.endsWith(".md"))
      .map((entry) => entry.name);
  } catch {
    return [];
  }
};

// Accepts both a YAML list and the comma-separated string used by pi's subagent example.
const parseTools = (raw: unknown): string[] | null => {
  if (raw == null) return null;

  if (typeof raw === "string") {
    const tools = raw
      .split(",")
      .map((tool) => tool.trim())
      .filter((tool) => tool.length > 0);

    return tools.length > 0 ? tools : null;
  }

  if (Array.isArray(raw) && raw.every((tool): tool is string => typeof tool === "string")) {
    return raw.length > 0 ? raw : null;
  }

  return null;
};

// A model reference resolves at delegation time; here we only require a non-empty string,
// so a malformed value never drops the rest of the agent — it just reverts to the default tier.
const parseModel = (raw: unknown): string | null => {
  if (raw == null) return null;

  if (typeof raw === "string") {
    const model = raw.trim();
    return model.length > 0 ? model : null;
  }

  return null;
};

/**
 * Discover agent definitions bundled inside skill packages: markdown files under
 * each skill's agents/ directory, with frontmatter metadata (description required;
 * name, tools, and model optional) and the body as the agent's system prompt.
 *
 * Invalid agent files are logged and skipped so one bad definition never blocks the rest.
 */
export const discoverSkillAgents = (skillsRoot: string, log: Logger): SkillAgent[] => {
  const agents: SkillAgent[] = [];

  for (const skill of listDirectories(skillsRoot)) {
    const agentsDir = join(skillsRoot, skill, "agents");

    for (const file of listMarkdownFiles(agentsDir)) {
      try {
        const { frontmatter, body } = parseFrontmatter(readFileSync(join(agentsDir, file), "utf8"));

        if (typeof frontmatter.description !== "string" || frontmatter.description.length === 0) {
          log.warn({ skill, agent: file }, "skill agent missing description — skipped");
          continue;
        }

        const stem = file.slice(0, -".md".length);
        const name =
          typeof frontmatter.name === "string" && frontmatter.name.length > 0
            ? frontmatter.name
            : stem;

        const model = parseModel(frontmatter.model);

        if (frontmatter.model != null && model == null) {
          log.warn({ skill, agent: file }, "skill agent has invalid model — using default tier");
        }

        const tools = parseTools(frontmatter.tools);

        if (frontmatter.tools != null && tools == null) {
          log.warn(
            { skill, agent: file },
            "skill agent has invalid tools format — using default tool set",
          );
        }

        agents.push({
          name: `${skill}/${name}`,
          description: frontmatter.description,
          tools,
          model,
          systemPrompt: body,
          skill,
        });
      } catch (error) {
        log.warn({ err: error, skill, agent: file }, "failed to load skill agent — skipped");
      }
    }
  }

  log.debug({ count: agents.length }, "discovered skill agents");

  return agents;
};
