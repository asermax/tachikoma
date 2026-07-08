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
 * boundary inbound middleware (before the classifier), marks the message handled, and acks immediately
 * — there is no agent turn, so the decision label is baked into the ack text rather than carried as a
 * streamed header (see the plan's ack-vs-header decision). `/checkpoint` sets a checkpoint at the
 * main-line tip; `/back` summarizes the tangent taken since the checkpoint back into it.
 */

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
 * to the classifier, surfacing its "Checking conversation topic…" lead-in) — and falls back to an
 * exact-text match for any path that does not stamp the token.
 */
export const isCommand = (message: InboundMessage, name: string): boolean =>
  message.metadata.command === name || message.text.trim() === `/${name}`;

/**
 * `/checkpoint`: set a checkpoint at the current main-line tip (R1). One is active at a time — setting
 * a new tip overrides a prior checkpoint (R2). Idempotent at the tip: setting the same tip twice is a
 * no-op with a notice (R11 — manual and auto checkpointing coincide rather than conflict). Returns
 * true when the message was `/checkpoint` (handled), false otherwise.
 */
export const handleCheckpointCommand = (
  deps: CommandDeps,
  message: InboundMessage,
  trunk: TrunkInbound,
): boolean => {
  if (!isCommand(message, "checkpoint")) return false;

  message.metadata.handled = true;

  const leafId = getLeafId(trunk.session);
  if (leafId == null) {
    deps.deliver({ text: "⚠️ No conversation to checkpoint yet.", immediate: true });
    return true;
  }

  // Idempotent at the tip: a checkpoint is already active here with no tangent taken since (R11 —
  // manual + auto checkpointing coincide rather than conflict). `checkpointHasTangent` is robust to the
  // boomerang entry `setCheckpoint` appends (which advances the leaf past the checkpoint message).
  const activeCheckpoint = trunk.checkpointId;
  if (activeCheckpoint != null && !checkpointHasTangent(trunk.session, activeCheckpoint)) {
    deps.deliver({ text: "ℹ️ Checkpoint already at this tip.", immediate: true });
    return true;
  }

  setCheckpointAndFocus(trunk.session, leafId, deps.log);
  deps.deliver({ text: "📌 Checkpoint set — main line parked here.", immediate: true });

  deps.log.info({ checkpointId: leafId }, "checkpoint set (/checkpoint)");
  return true;
};

/**
 * `/back`: summarize the tangent taken since the checkpoint back into it (R3/R4). Edge guards first
 * (R11): no active checkpoint ⇒ no-op + notice; a checkpoint with no tangent to summarize ⇒ no-op +
 * notice and the collapse primitive is NOT called (KD2 empty-tangent guard). Otherwise the tangent is
 * summarized away and the main line resumes at the checkpoint (R5). Returns true when the message was
 * `/back` (handled), false otherwise.
 */
export const handleBackCommand = async (
  deps: CommandDeps,
  message: InboundMessage,
  trunk: TrunkInbound,
): Promise<boolean> => {
  if (!isCommand(message, "back")) return false;

  message.metadata.handled = true;

  const checkpointId = trunk.checkpointId;

  // No active checkpoint: nothing to summarize back to.
  if (checkpointId == null) {
    deps.deliver({ text: "ℹ️ No checkpoint to summarize to.", immediate: true });
    return true;
  }

  // Empty-tangent guard (KD2): the leaf is still the checkpoint, so there is no tangent to fold away —
  // short-circuit before the append-only collapse primitive would create a vacuous summary.
  if (!checkpointHasTangent(trunk.session, checkpointId)) {
    deps.deliver({ text: "ℹ️ No tangent to summarize.", immediate: true });
    return true;
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
    return true;
  }

  deps.deliver({ text: "↩️ Summarized to checkpoint — back on the main line.", immediate: true });
  return true;
};
