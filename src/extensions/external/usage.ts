import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for third-party extension management, injected into the main session's
 * context. Scoped main-only like the tools it documents (default scope at registration) —
 * managing the extension set is a conversational action, not something a background run
 * does.
 */
export const EXTERNAL_USAGE = `## Extensions

Third-party extensions can be added to the harness — installed from a git URL or local path, updated, listed, and uninstalled with the extension tools. Installs and removals take effect on the next restart — after installing or uninstalling one, tell the person a restart is needed.

${referencePointer(import.meta.dirname, "extensions")}`;
