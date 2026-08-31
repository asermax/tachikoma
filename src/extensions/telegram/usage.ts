import { referencePointer } from "../../agent/prompt-references.ts";

/**
 * Usage guidance for the Telegram affordances, injected into the main session's context.
 * Registered only on the configured path (the extension sets up nothing when no bot token
 * and chat are given), next to the tools it documents — default main scope, like them.
 */
export const TELEGRAM_USAGE = `## Telegram

When talking over Telegram you have chat affordances beyond plain text — sending files, emoji reactions, pins, and tappable inline choice buttons. Prefer them where they fit: a reaction for a quick acknowledgement, buttons for a small choice, a file instead of pasted contents.

${referencePointer(import.meta.dirname, "telegram")}`;
