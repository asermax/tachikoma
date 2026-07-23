/**
 * Emoji reactions that surface each topic-boundary decision on the bot's own message, replacing the
 * inline italic banner (DecisionHeader text) and the immediate command-ack text.
 *
 * Telegram restricts `setMessageReaction` to a FIXED set of reaction emoji. The original banner emojis
 * — 🆕 (new topic), 📌 (checkpoint), ↩️ (summarize), 🔄 (rollback) — are NOT in that set and would be
 * rejected with `400 MESSAGE_REACTION_INVALID`, silently dropping the feedback. These are chosen from
 * the allowed reactions instead. The channel sets them best-effort, so a future API change never breaks
 * a boundary — the decision still takes effect, only the reaction is skipped.
 *
 * Shared across the auto middleware (`index.ts`), the manual commands (`commands.ts`), and `/rollback`
 * (`rollback.ts`) so an auto and manual occurrence of the same boundary (e.g. a classifier checkpoint
 * vs. `/checkpoint`) surface the same emoji.
 */
export const BOUNDARY_REACTIONS = {
  /** A new topic started (auto topic shift). */
  newTopic: "🔥",
  /** Checkpoint set (auto set-checkpoint, system-origin side task, or manual `/checkpoint`). */
  checkpointSet: "💔",
  /** Tangent summarized back to the checkpoint (auto summarize-to-checkpoint, or manual `/back`). */
  summarizedToCheckpoint: "❤",
  /** Most-recent automatic decision reversed (`/rollback`). */
  rolledBack: "👻",
} as const;
