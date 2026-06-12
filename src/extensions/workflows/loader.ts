import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { parseFrontmatter } from "@earendil-works/pi-coding-agent";

import type { Logger } from "../../log.ts";

export interface StepDefinition {
  /** Step identifier (matches the directory name). */
  id: string;
  title: string;
  instructionsPath: string;
  referencesPath: string | null;
  scriptsPath: string | null;
  /** When false, the step can be skipped. */
  required: boolean;
  /** Extensible frontmatter fields beyond the reserved keys. */
  properties: Record<string, unknown>;
}

export interface WorkflowDefinition {
  skillName: string;
  workflowName: string;
  /** Ordered by step directory name (e.g. 01-plan, 02-execute). */
  steps: StepDefinition[];
  path: string;
}

const RESERVED_STEP_KEYS = ["title", "required", "skippable"];

const isDirectory = (path: string): boolean => {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
};

const listDirectories = (path: string): string[] => {
  try {
    return readdirSync(path, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch {
    return [];
  }
};

const loadStep = (
  stepDir: string,
  stepId: string,
  context: { skillName: string; workflowName: string },
  log: Logger,
): StepDefinition | null => {
  const instructionsPath = join(stepDir, "instructions.md");

  if (!existsSync(instructionsPath)) {
    log.warn({ ...context, step: stepId }, "step missing instructions.md — skipped");
    return null;
  }

  let frontmatter: Record<string, unknown>;

  try {
    frontmatter = parseFrontmatter(readFileSync(instructionsPath, "utf8")).frontmatter;
  } catch (error) {
    log.warn({ err: error, ...context, step: stepId }, "failed to parse instructions.md — skipped");
    return null;
  }

  const title = frontmatter.title;

  if (typeof title !== "string" || title.length === 0) {
    log.warn({ ...context, step: stepId }, "step has missing or invalid title — skipped");
    return null;
  }

  let required = true;

  if ("required" in frontmatter) {
    if (typeof frontmatter.required !== "boolean") {
      log.warn({ ...context, step: stepId }, "step has invalid required type — skipped");
      return null;
    }

    required = frontmatter.required;
  } else if ("skippable" in frontmatter) {
    // skippable is the deprecated alias for required: false
    log.warn(
      { ...context, step: stepId },
      "step uses deprecated 'skippable' field; use 'required: false' instead",
    );
    required = frontmatter.skippable !== true;
  }

  const properties = Object.fromEntries(
    Object.entries(frontmatter).filter(([key]) => !RESERVED_STEP_KEYS.includes(key)),
  );

  const referencesPath = join(stepDir, "references");
  const scriptsPath = join(stepDir, "scripts");

  return {
    id: stepId,
    title,
    instructionsPath,
    referencesPath: isDirectory(referencesPath) ? referencesPath : null,
    scriptsPath: isDirectory(scriptsPath) ? scriptsPath : null,
    required,
    properties,
  };
};

const loadWorkflow = (
  workflowDir: string,
  skillName: string,
  workflowName: string,
  log: Logger,
): WorkflowDefinition => {
  const steps = listDirectories(workflowDir)
    .sort()
    .map((stepId) => loadStep(join(workflowDir, stepId), stepId, { skillName, workflowName }, log))
    .filter((step) => step != null);

  return { skillName, workflowName, steps, path: workflowDir };
};

/** Load every workflow defined under a skill directory's workflows/ subdirectory. */
export const loadSkillWorkflows = (
  skillDir: string,
  skillName: string,
  log: Logger,
): WorkflowDefinition[] => {
  const workflowsDir = join(skillDir, "workflows");

  return listDirectories(workflowsDir).map((workflowName) =>
    loadWorkflow(join(workflowsDir, workflowName), skillName, workflowName, log),
  );
};

/**
 * Resolve a single workflow definition from the skills root, reading the
 * filesystem fresh on every call so definition edits apply without a restart.
 */
export const findWorkflow = (
  skillsRoot: string,
  skillName: string,
  workflowName: string,
  log: Logger,
): WorkflowDefinition | null => {
  const workflowDir = join(skillsRoot, skillName, "workflows", workflowName);

  if (!isDirectory(workflowDir)) return null;

  return loadWorkflow(workflowDir, skillName, workflowName, log);
};
