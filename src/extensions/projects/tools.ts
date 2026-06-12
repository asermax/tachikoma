import { access } from "node:fs/promises";
import { join } from "node:path";

import type { ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import type { Logger } from "../../log.ts";
import {
  addSubmodule,
  checkoutBranch,
  describeProjectState,
  listSubmodules,
  projectState,
  removeSubmodule,
  resolveDefaultBranch,
  uncommittedChangesDetail,
} from "./git.ts";

export interface ProjectToolDeps {
  workspaceRoot: string;
  log: Logger;
}

export const RegisterProjectParams = Type.Object({
  name: Type.String({ description: "Project name — becomes the directory under projects/" }),
  url: Type.String({ description: "Git remote URL to clone the project from" }),
});

export const DeregisterProjectParams = Type.Object({
  name: Type.String({ description: "Name of the registered project to remove" }),
  force: Type.Optional(
    Type.Boolean({
      description: "Remove even when the project has uncommitted changes (they will be lost)",
    }),
  ),
});

export const ListProjectsParams = Type.Object({});

const exists = async (path: string): Promise<boolean> =>
  access(path).then(
    () => true,
    () => false,
  );

export const handleRegisterProject = async (
  { workspaceRoot, log }: ProjectToolDeps,
  args: Static<typeof RegisterProjectParams>,
): Promise<string> => {
  if (args.name === "") throw new Error("'name' is required");
  if (args.url === "") throw new Error("'url' is required");

  const projectPath = join(workspaceRoot, "projects", args.name);

  if (await exists(projectPath)) throw new Error(`Project '${args.name}' already exists`);

  try {
    await addSubmodule(workspaceRoot, args.name, args.url);

    const defaultBranch = await resolveDefaultBranch(projectPath);
    await checkoutBranch(projectPath, defaultBranch);

    log.info({ name: args.name, url: args.url, branch: defaultBranch }, "project registered");

    return (
      `Registered project '${args.name}' (branch: ${defaultBranch}). ` +
      `The project is now available under projects/${args.name}. ` +
      "Changes will be committed to the workspace at session end."
    );
  } catch (error) {
    if (await exists(projectPath)) {
      try {
        await removeSubmodule(workspaceRoot, args.name);
      } catch {
        // Partial-state cleanup is best-effort; the original error matters more.
      }
    }

    log.warn({ name: args.name, err: error }, "project registration failed");
    throw new Error(`Error registering project: ${(error as Error).message}`);
  }
};

export const handleDeregisterProject = async (
  { workspaceRoot, log }: ProjectToolDeps,
  args: Static<typeof DeregisterProjectParams>,
): Promise<string> => {
  if (args.name === "") throw new Error("'name' is required");

  const projectPath = join(workspaceRoot, "projects", args.name);

  if (!(await exists(projectPath))) throw new Error(`Project '${args.name}' not found`);

  const force = args.force ?? false;
  const changes = await uncommittedChangesDetail(projectPath);

  if (changes != null && !force) {
    throw new Error(
      `Project '${args.name}' has uncommitted changes:\n${changes}\n\n` +
        "Use force=true to remove anyway (changes will be lost).",
    );
  }

  await removeSubmodule(workspaceRoot, args.name);

  log.info({ name: args.name, force }, "project deregistered");

  return (
    `Deregistered project '${args.name}'. ` +
    "Changes will be committed to the workspace at session end."
  );
};

export const handleListProjects = async ({ workspaceRoot }: ProjectToolDeps): Promise<string> => {
  const submodulePaths = await listSubmodules(workspaceRoot);

  if (submodulePaths.length === 0) {
    return "No projects registered. Use register_project to add one.";
  }

  const lines = await Promise.all(
    submodulePaths.map(async (path) =>
      describeProjectState(await projectState(workspaceRoot, path)),
    ),
  );

  return `# Registered Projects\n\n${lines.join("\n")}`;
};

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

/** pi extension factory exposing the project management tools to the agent. */
export const createProjectsToolsFactory =
  (deps: ProjectToolDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "register_project",
      label: "Register Project",
      description:
        "Register an external git repository as a project: clones it as a submodule under projects/<name> and checks out its default branch. Project changes are committed and pushed automatically at session end.",
      promptSnippet: "Register an external git repository as a tracked project",
      promptGuidelines: [
        "Use register_project when the user wants to work on an external repository inside the workspace.",
      ],
      parameters: RegisterProjectParams,
      async execute(_toolCallId, params) {
        return textResult(await handleRegisterProject(deps, params));
      },
    });

    pi.registerTool({
      name: "deregister_project",
      label: "Deregister Project",
      description:
        "Remove a registered project from the workspace. Fails when the project has uncommitted changes unless force=true is passed (those changes are lost).",
      promptSnippet: "Remove a registered project from the workspace",
      promptGuidelines: [
        "Confirm with the user before calling deregister_project with force=true — uncommitted work is lost.",
      ],
      parameters: DeregisterProjectParams,
      async execute(_toolCallId, params) {
        return textResult(await handleDeregisterProject(deps, params));
      },
    });

    pi.registerTool({
      name: "list_projects",
      label: "List Projects",
      description:
        "List the registered projects with their current branch (or detached commit) and uncommitted-change counts.",
      promptSnippet: "List registered projects and their git state",
      promptGuidelines: [
        "Check list_projects before register_project to avoid registering a duplicate.",
      ],
      parameters: ListProjectsParams,
      async execute() {
        return textResult(await handleListProjects(deps));
      },
    });
  };
