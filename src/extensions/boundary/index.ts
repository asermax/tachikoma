import { Type } from "typebox";

import { checkpointHasTangent, getLeafId } from "../../agent/session-tree.ts";
import type { Delivery } from "../../channels/types.ts";
import type { DecisionHeader, InboundMessage } from "../../domain/message.ts";
import { SESSION_TOPIC_CHANGED_EVENT, type TopicChangedReason } from "../../events.ts";
import { type BranchRecord, recordLastAutoDecision } from "../../sessions/trunk.ts";
import { defineExtension } from "../api.ts";
import { createAskBranchFactory } from "./ask-branch.ts";
import { classifyShift } from "./classifier.ts";
import { collapseCurrentTopic, summarizeCurrentTangent } from "./collapse.ts";
import { handleBackCommand, handleCheckpointCommand } from "./commands.ts";
import { setCheckpointAndFocus } from "./focus.ts";
import { findRelatedBranch, injectRelatedBranchContext } from "./related.ts";
import { handleRollbackCommand, type RollbackDeps } from "./rollback.ts";

interface BoundaryConfig {
  enabled: boolean;
  /** Kill-switch for the automatic `set-checkpoint` classifier result (DLT-181, KD8). */
  autoSetCheckpoint: boolean;
  /** Kill-switch for the automatic `summarize-to-checkpoint` classifier result (DLT-181, KD8). */
  autoSummarizeToCheckpoint: boolean;
}

/**
 * The "📌 Checkpoint set" decision header shared by every automatic checkpoint site. The note is fixed;
 * only `rollbackable` varies (the classifier's auto `set-checkpoint` is a `/rollback` target; the
 * system-origin side-task checkpoint is not — system turns aren't user messages for the immediacy
 * counter). One source of truth keeps the wording from drifting between sites.
 */
const checkpointSetHeader = (rollbackable: boolean): DecisionHeader => ({
  label: "📌 Checkpoint set",
  note: "A side task started — the main line is parked here.",
  rollbackable,
});

/**
 * Conversation boundaries on the daily trunk. On each idle user message a shadow-fork
 * classifier decides whether the message continues the current topic or starts a new one; a shift
 * collapses the current branch into a `branch_summary` on the trunk and may pull a related prior
 * branch's context. The `ask_branch` tool answers focused questions from any prior branch. Detection
 * is non-invasive (the live session is never mutated by the classifier) and fails open.
 */
export default defineExtension<BoundaryConfig>({
  name: "boundary",

  configSchema: Type.Object({
    // Gates topic-shift detection. Forced shifts ("/new") are honored even when detection is off.
    enabled: Type.Boolean({ default: true }),
    // Per-result kill-switches (KD8): each auto checkpoint result can be disabled independently without
    // losing manual `/checkpoint`,`/back` or topic detection. Both default on (conservative-on).
    autoSetCheckpoint: Type.Boolean({ default: true }),
    autoSummarizeToCheckpoint: Type.Boolean({ default: true }),
  }),

  setup(app) {
    const {
      enabled: detectionEnabled,
      autoSetCheckpoint,
      autoSummarizeToCheckpoint,
    } = app.extensionConfig;

    if (!detectionEnabled) {
      app.log.info("boundary detection disabled by configuration");
    }

    // `ask_branch` resolves its target on the live trunk session the coordinator owns.
    app.agent.use(
      createAskBranchFactory({
        getTrunkSession: () => app.sessions.activeTrunkSession(),
        shadowFork: app.agent.shadowFork,
        branchFile: app.agent.branchFile,
        log: app.log,
      }),
      { sessionScopes: ["main"] },
    );

    // Stateless command deps, shared across the manual command handlers and built once in setup scope
    // rather than rebuilt per message. `/rollback` adds the coordinator `replay` seam to the same set.
    const commandDeps = {
      side: app.agent.side,
      log: app.log,
      deliver: (delivery: Delivery) => app.channels.deliver(delivery),
    };
    const rollbackDeps: RollbackDeps = {
      ...commandDeps,
      replay: (text, header) => app.sessions.replay(text, header),
    };

    app.inbound.use(async (message, context, next) => {
      const trunk = context.trunk;

      // Turns with no live trunk never shift topics — they append with detection skipped (R13).
      if (trunk == null) return next();

      // A `boundary: "skip"` message appends with classification skipped. System-origin side tasks
      // (queue digests, fired session tasks) are the exception: they begin a new interactive turn that
      // historically got absorbed into the main branch. When the main line is parkable, checkpoint it so
      // the side task runs as a tangent instead of diluting the main line (issue-411). Replays are
      // excluded — rollback already applied the correct framing (a checkpoint in Case B, a topic in Case
      // A) — so the rule keys on the `replay` marker the coordinator stamps. Gated on `autoSetCheckpoint`
      // (the same kill-switch as the classifier's auto set-checkpoint) and the parkable-main-line guards.
      if (message.metadata.boundary === "skip") {
        if (
          message.metadata.origin === "system" &&
          message.metadata.replay !== true &&
          trunk.checkpointId == null &&
          trunk.hasAssistantTurnSinceBase &&
          autoSetCheckpoint
        ) {
          const leaf = getLeafId(trunk.session);
          if (leaf != null) {
            setCheckpointAndFocus(trunk.session, leaf, app.log);
            message.metadata.decisionHeader = checkpointSetHeader(false);
          }
        }

        return next();
      }

      // Manual checkpoint commands (DLT-181): detected before any branching logic. Each marks the
      // message handled and acks immediately (no agent turn, no stream — the decision label is baked
      // into the ack text). `/checkpoint` parks the main line at the tip; `/back` folds the tangent
      // back into the checkpoint so the main line resumes intact.
      if (handleCheckpointCommand(commandDeps, message, trunk)) return;
      if (await handleBackCommand(commandDeps, message, trunk)) return;

      // `/rollback` (DLT-181): reverse the most-recent automatic checkpoint/topic decision. Detected
      // before the classifier like the other manual commands. On a no-op it acks the notice (handled);
      // on a successful reversal it marks the message handled (the command itself does not stream) and
      // replays the triggering message — whose streamed response carries the rollback header — via the
      // coordinator replay (bypasses submit, so no re-classification of the corrected framing).
      if (await handleRollbackCommand(rollbackDeps, message, trunk)) return;

      // Collapse the live branch into a `branch_summary`, unless it has no assistant turn yet
      // (empty-branch guard: a shift off an empty branch starts the new branch without an empty summary).
      const collapseLiveBranch = (
        status: string,
        reason?: string,
      ): Promise<{ newBaseId: string } | null> | undefined => {
        if (!trunk.hasAssistantTurnSinceBase) return undefined;

        app.status(status);

        return collapseCurrentTopic(
          { side: app.agent.side, log: app.log },
          {
            session: trunk.session,
            currentBaseId: trunk.currentBaseId,
            branchId: trunk.liveBranchId,
            reason,
          },
        );
      };

      // Unified "start a new branch" used by both the auto-detected topic shift and the manual `/new`,
      // so the two share the same collapse summary, status text, related-branch context injection,
      // "topic shifted" log, and topic-changed signal. The auto-shift caller layers its `/rollback`
      // bookkeeping on top (R7 — manual `/new` is intentionally not a rollback target). Emits
      // `session:topic-changed` so downstream consumers (proactive skill injection) reset per-branch
      // state for the new branch — restoring the per-topic fresh evaluation the old session-per-topic
      // model gave for free under the daily-trunk model. The signal fires after the new base is
      // established (matching the earlier-branch path below) and unconditionally — a new branch starts
      // whether or not the prior branch produced a collapse summary (empty-branch guard).
      const startNewBranch = async (args: {
        message: InboundMessage;
        reason: TopicChangedReason;
        /** Human-readable collapse provenance recorded on the branch summary (distinct from the event reason). */
        collapseReason?: string;
      }): Promise<{ newBaseId: string } | null | undefined> => {
        const collapsed = await collapseLiveBranch("Starting a new topic", args.collapseReason);

        // Signal after the collapse resolves so the phase matches the earlier-branch path and a future
        // consumer reading post-collapse trunk state sees the fresh branch.
        app.events.emit(SESSION_TOPIC_CHANGED_EVENT, { reason: args.reason });

        let related: BranchRecord | null = null;
        if (collapsed != null) {
          related = await findRelatedBranch(
            { side: app.agent.side, log: app.log },
            {
              session: trunk.session,
              branchRecords: trunk.branchRecords,
              message: args.message.text,
            },
          );

          if (related != null) injectRelatedBranchContext(trunk.session, related, app.log);
        }

        app.log.info(
          {
            collapsedBranchId: trunk.liveBranchId,
            newBaseId: collapsed?.newBaseId ?? null,
            relatedBranchId: related?.branchId ?? null,
          },
          "topic shifted",
        );

        return collapsed;
      };

      // Forced reply/reaction/button reference (Telegram resolves a referenced message to its branch).
      // The reference is an explicit, deterministic signal of intent, so it bypasses the classifier:
      // same branch → append; earlier branch → forced collapse + new branch + inject that branch's
      // context. An unrecorded target carries no forced metadata and falls through to detection below.
      if (typeof message.metadata.forcedBranchId === "string") {
        const forcedBranchId = message.metadata.forcedBranchId;
        const referenced = trunk.branchRecords.find((record) => record.branchId === forcedBranchId);

        // A reference to the live (un-collapsed) branch is in the current conversation already → append.
        if (forcedBranchId === trunk.liveBranchId) return next();

        // A recorded id that resolves to a known earlier branch forces a shift + context injection;
        // an id that no longer resolves to a record (e.g. a stale routing row) falls through to the
        // classifier below rather than silently appending.
        app.log.debug(
          { forcedBranchId, resolved: referenced != null, liveBranchId: trunk.liveBranchId },
          "forced branch reference",
        );

        if (referenced != null) {
          await collapseLiveBranch(
            "Switching to an earlier topic",
            `user referenced ${forcedBranchId}`,
          );
          // Jumping to an earlier branch is also a topic change — signal it so downstream per-branch
          // state resets for the branch being resumed.
          app.events.emit(SESSION_TOPIC_CHANGED_EVENT, { reason: "earlier-branch" });
          injectRelatedBranchContext(trunk.session, referenced, app.log);

          return next();
        }
      }

      // "/new": force a topic shift. Honored even when detection is off, so the user can always start over.
      // Shares the same collapse summary, status, related-branch injection, and topic-changed signal as an
      // auto-detected shift (startNewBranch); only the `/rollback` bookkeeping differs (auto-only, R7).
      if (message.metadata.forceNew === true) {
        app.log.info({ branchId: trunk.liveBranchId }, "forced new topic (/new)");

        await startNewBranch({
          message,
          reason: "/new",
          collapseReason: "user forced a new topic (/new)",
        });

        return next();
      }

      if (!detectionEnabled) return next();

      app.status("Checking conversation topic…");

      const decision = await classifyShift(
        {
          shadowFork: app.agent.shadowFork,
          getSystemPrompt: () => trunk.session.systemPrompt,
          log: app.log,
        },
        {
          sessionFile: trunk.sessionFile,
          currentBranchHasAssistantTurn: trunk.hasAssistantTurnSinceBase,
          message: message.text,
          // The classifier runs on a detached shadowFork and cannot read the live trunk, so the active
          // checkpoint state is injected (S3). It gates which checkpoint decision is even offered.
          checkpointActive: trunk.checkpointId != null,
        },
      );

      app.log.debug(
        { branchId: trunk.liveBranchId, decision, messageLen: message.text.length },
        "topic-shift classification",
      );

      // Auto side-conversation decisions (DLT-181, R6). Each is gated defensively on the checkpoint
      // state the classifier cannot see and on its per-result kill-switch (KD8); a suppressed result
      // degrades to "continue" (the message is answered normally). The message is NOT handled here — it
      // streams as the first tangent turn (set-checkpoint) or the resumed main-line turn
      // (summarize-to-checkpoint) — so the decision surfaces via the turn-scoped header on that response.
      if (decision === "set-checkpoint") {
        if (autoSetCheckpoint && trunk.checkpointId == null) {
          const leaf = getLeafId(trunk.session);
          if (leaf != null) {
            // Capture the leaf BEFORE setCheckpoint: its boomerang append advances the leaf past the
            // checkpoint message, and preDecisionLeafId must name the tip before the triggering exchange.
            recordLastAutoDecision(trunk.session, "set-checkpoint", leaf);
            setCheckpointAndFocus(trunk.session, leaf, app.log);
            message.metadata.decisionHeader = checkpointSetHeader(true);
          }
        }

        return next();
      }

      if (decision === "summarize-to-checkpoint") {
        const checkpointId = trunk.checkpointId;
        // Defensive gating: only valid with an active checkpoint that has a tangent to fold (the
        // classifier prompt is checkpoint-aware, but this guard never creates a vacuous summary — KD2).
        if (
          autoSummarizeToCheckpoint &&
          checkpointId != null &&
          checkpointHasTangent(trunk.session, checkpointId)
        ) {
          const result = await summarizeCurrentTangent(
            { side: app.agent.side, log: app.log },
            { session: trunk.session, checkpointId },
          );

          if (result != null) {
            // summarizeCurrentTangent already folds the tangent and clears the checkpoint (R3/R5).
            message.metadata.decisionHeader = {
              label: "↩️ Summarized to checkpoint",
              note: "Back on the main line; the side topic was folded away.",
              rollbackable: false,
            };
          }
        }

        return next();
      }

      if (decision === "shift") {
        // Capture the leaf before the collapse: it is the tip the triggering exchange extends, i.e. the
        // pre-decision point /rollback rewinds to (Batch 4). Recorded only on a successful auto collapse.
        const preDecisionLeafId = getLeafId(trunk.session);

        const collapsed = await startNewBranch({
          message,
          reason: "auto-shift",
        });

        // An automatic topic shift is a /rollback target (Batch 4). Manual /new (forceNew above) does
        // NOT record it — only automatic decisions are rollback targets (R7). The decision surfaces on
        // the shifted response via the turn-scoped header, which signals /rollback is available — set
        // it exactly where the decision is recorded so the two can never disagree (R8). The shared
        // "topic shifted" log is emitted inside startNewBranch.
        if (collapsed != null && preDecisionLeafId != null) {
          recordLastAutoDecision(trunk.session, "new", preDecisionLeafId);
          message.metadata.decisionHeader = {
            label: "🆕 New topic",
            note: "Started a fresh topic — the previous one was collapsed.",
            rollbackable: true,
          };
        }
      }

      return next();
    });
  },
});
