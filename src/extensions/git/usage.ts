import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for workspace git management, injected into the agent's context.
 * Scoped to main + background. The critical rules stay inline (auto-commit, dedicated
 * tools); the bash deny list and recovery mechanics live in the reference file.
 */
export const GIT_USAGE = `## Git

Your workspace is a git repository and versioning is handled for you: changes (and dirty project submodules) are committed with a generated message at conversation close and pushed where a remote exists. You do not need to commit or push by hand for normal work.

To save or publish now instead of waiting for close, use \`commit_workspace\` (pass \`push=false\` to commit only). Mutating git through bash is deliberately blocked — for state-changing git, use the dedicated tools (\`commit_workspace\`; \`scrub\` to purge paths from history).

${referencePointer(import.meta.dirname, "git")}`;
