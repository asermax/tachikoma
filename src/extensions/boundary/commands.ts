import { checkpointHasTangent, getLeafId } from "../../agent/session-tree.ts";
import type { SideRunner } from "../../agent/side-run.ts";
import type { Delivery } from "../../channels/types.ts";
import type { InboundMessage } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import type { TrunkInbound } from "../api.ts";
import { summarizeCurrentTangent } from "./collapse.ts";
import { setCheckpointAndFocus } from "./focus.ts";

/**
 * Manual checkpoint commands (`/checkpoint`, `/back`) for DLT-181. Each is detected at the top of the
 * boundary inbound middleware (before the classifier), performs its action, and either acks immediately
 * (no agent turn — the decision label is baked into the ack text rather than carried as a streamed
 * header) or, when the user sent trailing text, strips it onto the message and signals `"continue"` so
 * it streams as the first turn of the tangent (`/checkpoint`) or the resumed main line (`/back`) — the
 * same prefix-strip + `next()` flow `/new` uses. `/rollback` intentionally does NOT take trailing text
 * (it replays the original triggering turn); it lives in `rollback.ts` and keeps its boolean contract.
 */

/**
 * The disposition a manual command handler returns to the middleware:
 * - `"unhandled"` — not this command; the middleware falls through to the next handler/classifier.
 * - `"acked"` — command fired with no trailing turn (bare success or a guard-failure notice); stop.
 * - `"continue"` — command succeeded and trailing text was stripped onto `message.text`; the middleware
 *   must `next()` so it streams as a turn, skipping the classifier (the user chose the transition).
 */
export type ManualCommandOutcome = "unhandled" | "acked" | "continue";

export interface CommandDeps {
  side: Pick<SideRunner, "complete">;
  log: Logger;
  /** Immediate ack delivery (synchronous channel render for command UI, bypassing the queue). */
  deliver: (delivery: Delivery) => void;
}

/**
 * Whether `message` is the `/${name}` manual command. Dispatches on the channel-stamped
 * `metadata.command` token — robust to a reply quote, trailing argument, or unresolved-reply hint
 * prepended to `text` (any of which would defeat an exact `text` match and let the command fall through
 * to the classifier, surfacing its "Checking conversation topic…" lead-in) — and falls back to a
 * leading-text match (`/<name>` optionally followed by a trailing argument) for any path that does not
 * stamp the token. `/<name>xyz` (a different command) is not matched.
 */
export const isCommand = (message: InboundMessage, name: string): boolean =>
  message.metadata.command === name || new RegExp(`^/${name}(?:$|\\s)`).test(message.text.trim());

/**
 * The trailing argument after a `/${name}` command (trimmed), or "" when bare. Call only after
 * {@link isCommand} confirms the command. The command token is the user's actual input, appended last in
 * `text` (after any reply quote the channel prepended), so the regex anchors to the *last* `/<name>` —
 * a quote that itself contains the token never captures the wrong tail.
 */
export const commandArgument = (message: InboundMessage, name: string): string => {
  const match = new RegExp(`.*\\/${name}(?:\\s+(.*))?$`, "s").exec(message.text);
  return match?.[1]?.trim() ?? "";
};

/**
 * Mark the message handled and return `"acked"` — no turn streams. Routed through by both manual
 * handlers at every guard-failure and bare-success return, so the "continue path leaves `handled`
 * unset" invariant holds by construction rather than by remembering to set it at each site.
 */
const ack = (message: InboundMessage): ManualCommandOutcome => {
  message.metadata.handled = true;
  return "acked";
};

/**
 * Trailing-text policy for the success tail shared by both manual handlers. If the user sent an argument,
 * strip it onto the message and return `"continue"` so it streams as the first turn of the tangent
 * (`/checkpoint`) or the resumed main line (`/back`), skipping the classifier — the user explicitly chose
 * the transition. This is the same prefix-strip + `next()` flow `/new` uses (R10). A bare command has no
 * argument, so it falls through to {@link ack} (no turn streams).
 */
const ackOrContinue = (message: InboundMessage, argument: string): ManualCommandOutcome => {
  if (argument.length > 0) {
    message.text = argument;
    return "continue";
  }
  return ack(message);
};

/**
 * `/checkpoint`: set a checkpoint at the current main-line tip (R1). One is active at a time — setting
 * a new tip overrides a prior checkpoint (R2). Idempotent at the tip: setting the same tip twice is a
 * no-op with a notice (R11 — manual and auto checkpointing coincide rather than conflict). With trailing
 * text, the checkpoint is set (or already active) and the text streams as the tangent's first turn. On a
 * guard failure (no conversation) the notice stands and no turn streams.
 */
export const handleCheckpointCommand = (
  deps: CommandDeps,
  message: InboundMessage,
  trunk: TrunkInbound,
): ManualCommandOutcome => {
  if (!isCommand(message, "checkpoint")) return "unhandled";

  const argument = commandArgument(message, "checkpoint");

  const leafId = getLeafId(trunk.session);
  if (leafId == null) {
    // No conversation to checkpoint: the notice stands and no turn streams (D2 — trailing text only
    // makes sense once a checkpoint is in effect).
    deps.deliver({ text: "⚠️ No conversation to checkpoint yet.", immediate: true });
    return ack(message);
  }

  // Idempotent at the tip: a checkpoint is already active here with no tangent taken since (R11 —
  // manual + auto checkpointing coincide rather than conflict). `checkpointHasTangent` is robust to the
  // boomerang entry `setCheckpoint` appends (which advances the leaf past the checkpoint message). The
  // checkpoint is in effect either way, so trailing text still starts the tangent — only the ack differs.
  const activeCheckpoint = trunk.checkpointId;
  const alreadyAtTip =
    activeCheckpoint != null && !checkpointHasTangent(trunk.session, activeCheckpoint);
  if (alreadyAtTip) {
    deps.deliver({ text: "ℹ️ Checkpoint already at this tip.", immediate: true });
  } else {
    setCheckpointAndFocus(trunk.session, leafId, deps.log);
    deps.deliver({ text: "📌 Checkpoint set — main line parked here.", immediate: true });
    deps.log.info({ checkpointId: leafId }, "checkpoint set (/checkpoint)");
  }

  // Trailing text (if any) streams as the tangent's first turn; a bare command acks with no turn.
  return ackOrContinue(message, argument);
};

/**
 * `/back`: summarize the tangent taken since the checkpoint back into it (R3/R4). Edge guards first
 * (R11): no active checkpoint ⇒ no-op + notice; a checkpoint with no tangent to summarize ⇒ no-op +
 * notice and the collapse primitive is NOT called (KD2 empty-tangent guard). Otherwise the tangent is
 * summarized away and the main line resumes at the checkpoint (R5). With trailing text, the tangent is
 * folded and the text streams as the resumed main line's first turn. On a guard failure (or a summarize
 * that degraded) the notice stands and no turn streams.
 */
export const handleBackCommand = async (
  deps: CommandDeps,
  message: InboundMessage,
  trunk: TrunkInbound,
): Promise<ManualCommandOutcome> => {
  if (!isCommand(message, "back")) return "unhandled";

  const argument = commandArgument(message, "back");

  const checkpointId = trunk.checkpointId;

  // No active checkpoint: nothing to summarize back to. The notice stands and no turn streams (D2).
  if (checkpointId == null) {
    deps.deliver({ text: "ℹ️ No checkpoint to summarize to.", immediate: true });
    return ack(message);
  }

  // Empty-tangent guard (KD2): the leaf is still the checkpoint, so there is no tangent to fold away —
  // short-circuit before the append-only collapse primitive would create a vacuous summary.
  if (!checkpointHasTangent(trunk.session, checkpointId)) {
    deps.deliver({ text: "ℹ️ No tangent to summarize.", immediate: true });
    return ack(message);
  }

  const result = await summarizeCurrentTangent(
    { side: deps.side, log: deps.log },
    { session: trunk.session, checkpointId },
  );

  if (result == null) {
    // summarizeCurrentTangent degrades gracefully and leaves the checkpoint active.
    deps.deliver({
      text: "⚠️ Couldn't summarize the tangent — the checkpoint is still in place.",
      immediate: true,
    });
    return ack(message);
  }

  deps.deliver({ text: "↩️ Summarized to checkpoint — back on the main line.", immediate: true });

  // Trailing text (if any) streams as the resumed main line's first turn; a bare command acks with no turn.
  return ackOrContinue(message, argument);
};
