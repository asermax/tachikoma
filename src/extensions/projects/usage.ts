import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * The static guidance half of the `projects` context. Exported as the extension's usage
 * constant (DES-002 convention) so the static-content sweep and size budget enumerate it
 * mechanically; `buildProjectsContext` composes it with the session-start state snapshot.
 * Scoped main + background alongside the provider that injects it.
 */
export const PROJECTS_USAGE = `## Projects

External repositories are managed as git submodules under \`projects/\` — register them with \`register_project\`, inspect with \`list_projects\`, remove with \`deregister_project\`. They are synced on startup, and dirty projects are committed and pushed automatically at conversation close, so normal project work needs no manual git from you.

The state below is a snapshot from session start — use \`list_projects\` for the live picture.

${referencePointer(import.meta.dirname, "projects")}`;
