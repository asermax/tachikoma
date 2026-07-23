import type { AgentSession, SessionEntry } from "@earendil-works/pi-coding-agent";

import {
  branchEntriesSinceBase,
  getBranchEntries,
  messageText,
  reseatLeaf,
} from "../../agent/session-tree.ts";
import type { SideRunner } from "../../agent/side-run.ts";
import type { Delivery } from "../../channels/types.ts";
import type { DecisionHeader, InboundMessage } from "../../domain/message.ts";
import type { Logger } from "../../log.ts";
import {
  type AutoDecision,
  clearLastAutoDecision,
  getAllBranchRecords,
  getBranchRecords,
  markReversed,
  nextBranchId,
  readBoomerangState,
  setCurrentTopicBase,
} from "../../sessions/trunk.ts";
import type { TrunkInbound } from "../api.ts";
import { collapseCurrentTopic } from "./collapse.ts";
import { isCommand } from "./commands.ts";
import { setCheckpointAndFocus } from "./focus.ts";
import { BOUNDARY_REACTIONS } from "./reactions.ts";

/**
 * `/rollback` (DLT-181, R7) — the load-bearing flow (KD4). Reverses the most-recent *automatic*
 * `set-checkpoint`/`new` decision in exactly two cases by rewinding to the pre-decision tip via
 * `branch(id)` (the sanctioned, append-only tip-move — the wrong-framing exchange becomes an inert
 * off-path branch, never deleted), applying the opposite transition, and replaying the triggering
 * message as a fresh turn under the corrected framing. No-op + notice in every other case.
 *
 * The decision state lives on the session tree (`lastAutoDecision` in boomerang-state), so a restart
 * between the bad decision and `/rollback` (with no exchange in between) still rolls back — the
 * triggering message and immediacy are both read from the tree at rollback time.
 */

export interface RollbackDeps {
  side: Pick<SideRunner, "complete">;
  log: Logger;
  /** Immediate ack delivery (synchronous channel render for the no-op notice). */
  deliver: (delivery: Delivery) => void;
  /**
   * Re-run `text` as a fresh turn carrying `header` (the boundary → coordinator replay contract). Set
   * by the middleware to `app.sessions.replay`. Only invoked on a successful reversal — no-op cases
   * ack the notice instead.
   */
  replay: (text: string, header?: DecisionHeader) => void;
}

const NOOP_NOTICE = "ℹ️ Nothing to roll back.";

/** Entry ids on the active leaf path (root→leaf), for on-path checks (KD5: the decision marker is read from the tree). */
const leafPathIds = (session: AgentSession): Set<string> =>
  new Set(getBranchEntries(session).map((entry) => entry.id));

/** User-message entries strictly after `decisionPointId` on the leaf path — the immediacy signal. */
const userMessagesSince = (session: AgentSession, decisionPointId: string): SessionEntry[] =>
  branchEntriesSinceBase(session, decisionPointId).filter(
    (entry) => entry.type === "message" && (entry.message as { role?: string }).role === "user",
  );

interface ResolvedDecisionPoint {
  /**
   * The on-path entry marking the decision: for `set-checkpoint`, the pre-decision tip (the checkpoint
   * continued inline, so it stays on the path); for `new`, the topic summary the shift created (the
   * pre-decision tip itself went off-path when the shift collapsed, so the summary is the on-path
   * anchor). Null when the marker can no longer be resolved (stale) → no-op.
   */
  decisionPointId: string | null;
  /** The auto-`new`'s topic summary to orphan (Case B only); null otherwise. */
  reversedSummaryId: string | null;
  /** The base the Case-B restored branch extends (the reversed summary's own baseId). */
  restoredBaseId: string | null;
}

/**
 * Resolve the on-path decision marker for `decision`. Immediacy is measured from this point: the
 * triggering exchange is the first user turn after it, and "no exchange since" means exactly one user
 * message follows it (a later summarize appends a summary, not a user turn — counting user messages
 * avoids a false "not immediate"). Returns a null `decisionPointId` when state is ambiguous → no-op.
 */
const resolveDecisionPoint = (
  session: AgentSession,
  decision: AutoDecision,
): ResolvedDecisionPoint => {
  const none = (): ResolvedDecisionPoint => ({
    decisionPointId: null,
    reversedSummaryId: null,
    restoredBaseId: null,
  });
  const path = leafPathIds(session);

  if (decision.kind === "set-checkpoint") {
    // Case A: the checkpoint tip stayed on the live path (the tangent continued inline from it).
    return path.has(decision.preDecisionLeafId)
      ? {
          decisionPointId: decision.preDecisionLeafId,
          reversedSummaryId: null,
          restoredBaseId: null,
        }
      : none();
  }

  // Case B: the pre-decision tip is the abandoned leaf of the auto-new's topic summary (off-path after
  // the collapse). The on-path marker is that summary itself, found via its recorded originalLeafId.
  const record = getAllBranchRecords(session).find(
    (candidate) => candidate.originalLeafId === decision.preDecisionLeafId,
  );

  if (record == null || !path.has(record.summaryEntryId)) return none();

  return {
    decisionPointId: record.summaryEntryId,
    reversedSummaryId: record.summaryEntryId,
    restoredBaseId: record.baseId,
  };
};

/**
 * `/rollback`: reverse the most-recent automatic `set-checkpoint`/`new` decision (R7). Detected at the
 * top of the boundary inbound middleware (before the classifier). On a no-op it marks the message
 * handled and acks the notice; on a successful reversal it marks the message handled (so the command
 * itself does not stream) and replays the triggering message, whose streamed response carries the
 * rollback header. Returns true when the message was `/rollback` (handled), false otherwise.
 */
export const handleRollbackCommand = async (
  deps: RollbackDeps,
  message: InboundMessage,
  trunk: TrunkInbound,
): Promise<boolean> => {
  if (!isCommand(message, "rollback")) return false;

  message.metadata.handled = true;

  const decision = trunk.lastAutoDecision;
  // Only automatic set-checkpoint / new decisions are rollback targets (R7). Manual decisions,
  // `continue`, `summarize-to-checkpoint`, and an absent log all no-op.
  if (decision == null || (decision.kind !== "set-checkpoint" && decision.kind !== "new")) {
    deps.deliver({ text: NOOP_NOTICE, immediate: true });
    return true;
  }

  const session = trunk.session;
  const { decisionPointId, reversedSummaryId, restoredBaseId } = resolveDecisionPoint(
    session,
    decision,
  );

  // Stale / ambiguous decision marker (e.g. the checkpoint was since summarized away, which leaves no
  // user turn after it) → no-op. Default to no-op on any ambiguity (restart-safe: state is on the tree).
  if (decisionPointId == null) {
    deps.deliver({ text: NOOP_NOTICE, immediate: true });
    return true;
  }

  // Immediacy: exactly one user-message turn (the triggering exchange) follows the decision marker. Zero
  // (consumed/never answered) or more than one (a later turn happened) ⇒ no-op.
  const triggering = userMessagesSince(session, decisionPointId);
  if (triggering.length !== 1) {
    deps.deliver({ text: NOOP_NOTICE, immediate: true });
    return true;
  }

  const triggeringText = messageText(triggering[0] as SessionEntry);
  if (triggeringText === "") {
    deps.deliver({ text: NOOP_NOTICE, immediate: true });
    return true;
  }

  deps.log.info(
    { kind: decision.kind, preDecisionLeafId: decision.preDecisionLeafId },
    "rollback rewinding",
  );

  // Rewind: re-seat the leaf to the pre-decision tip. The wrong-framing triggering exchange + its
  // answer become an inert off-path branch (append-only; not deleted, not re-extracted — KD4/KD6).
  reseatLeaf(session, decision.preDecisionLeafId);

  let header: DecisionHeader;
  if (decision.kind === "set-checkpoint") {
    // Case A (set-checkpoint → topic): collapse the restored branch up to the tip as a TOPIC summary.
    // The replayed message becomes the new topic's first turn (re-answered under the topic framing).
    const currentBaseId = readBoomerangState(session)?.currentTopicBaseId ?? null;
    const branchId = nextBranchId(getBranchRecords(session));
    const collapsed = await collapseCurrentTopic(
      { side: deps.side, log: deps.log },
      { session, currentBaseId, branchId, reason: "rollback: undid an automatic checkpoint" },
    );

    if (collapsed == null) {
      // collapseCurrentTopic degrades gracefully (no partial writes). The rewind already staged; surface
      // it rather than replaying under a half-applied transition.
      deps.deliver({ text: "⚠️ Couldn't complete the rollback.", immediate: true });
      return true;
    }

    header = {
      label: "🔄 Rolled back to topic",
      note: "The side topic is now the main thread.",
      rollbackable: false,
      reaction: BOUNDARY_REACTIONS.rolledBack,
    };
  } else {
    // Case B (new → checkpoint): set a checkpoint at the restored tip, orphan the auto-new's topic
    // summary, and restore the base the reversed shift had advanced past. The replayed message becomes
    // the first tangent turn.
    setCheckpointAndFocus(session, decision.preDecisionLeafId, deps.log);
    if (reversedSummaryId != null) markReversed(session, reversedSummaryId);
    // The shift left currentTopicBaseId on the now-reversed summary; reopen would otherwise re-seat the
    // leaf onto that dead branch. Restore the base the live branch actually extends.
    setCurrentTopicBase(session, restoredBaseId);

    header = {
      label: "🔄 Rolled back to checkpoint",
      note: "The new topic is parked as a side topic instead.",
      rollbackable: false,
      reaction: BOUNDARY_REACTIONS.rolledBack,
    };
  }

  // The reversal is staged — clear the decision log so a second /rollback does not re-target it.
  clearLastAutoDecision(session);

  // Replay the triggering message under the corrected framing; its streamed response carries the header.
  deps.replay(triggeringText, header);

  deps.log.info({ kind: decision.kind }, "rollback staged — replaying triggering message");
  return true;
};
