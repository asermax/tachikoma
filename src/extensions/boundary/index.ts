import { Type } from "typebox";

import { checkpointHasTangent, getLeafId } from "../../agent/session-tree.ts";
import type { Delivery } from "../../channels/types.ts";
import { recordLastAutoDecision, setCheckpoint } from "../../sessions/trunk.ts";
import { defineExtension } from "../api.ts";
import { createAskBranchFactory } from "./ask-branch.ts";
import { classifyShift } from "./classifier.ts";
import { collapseCurrentTopic, summarizeCurrentTangent } from "./collapse.ts";
import { handleBackCommand, handleCheckpointCommand } from "./commands.ts";
import { findRelatedBranch, injectRelatedBranchContext } from "./related.ts";

interface BoundaryConfig {
  enabled: boolean;
  /** Kill-switch for the automatic `set-checkpoint` classifier result (DLT-181, KD8). */
  autoSetCheckpoint: boolean;
  /** Kill-switch for the automatic `summarize-to-checkpoint` classifier result (DLT-181, KD8). */
  autoSummarizeToCheckpoint: boolean;
}

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

    app.inbound.use(async (message, context, next) => {
      const trunk = context.trunk;

      // System-origin injections (session tasks, notices) and turns with no live trunk never shift
      // topics — they append to the current branch with detection skipped (R13).
      if (message.metadata.boundary === "skip" || trunk == null) return next();

      // Manual checkpoint commands (DLT-181): detected before any branching logic. Each marks the
      // message handled and acks immediately (no agent turn, no stream — the decision label is baked
      // into the ack text). `/checkpoint` parks the main line at the tip; `/back` folds the tangent
      // back into the checkpoint so the main line resumes intact.
      const commandDeps = {
        side: app.agent.side,
        log: app.log,
        deliver: (delivery: Delivery) => app.channels.deliver(delivery),
      };
      if (handleCheckpointCommand(commandDeps, message, trunk)) return;
      if (await handleBackCommand(commandDeps, message, trunk)) return;

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
          injectRelatedBranchContext(trunk.session, referenced, app.log);

          return next();
        }
      }

      // "/new": force a topic shift. Honored even when detection is off, so the user can always start over.
      if (message.metadata.forceNew === true) {
        app.log.info({ branchId: trunk.liveBranchId }, "forced new topic (/new)");

        await collapseLiveBranch("Starting a new topic", "user forced a new topic");

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
          checkpointActive: trunk.checkpointActive,
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
        if (autoSetCheckpoint && !trunk.checkpointActive) {
          const leaf = getLeafId(trunk.session);
          if (leaf != null) {
            // Capture the leaf BEFORE setCheckpoint: its boomerang append advances the leaf past the
            // checkpoint message, and preDecisionLeafId must name the tip before the triggering exchange.
            recordLastAutoDecision(trunk.session, "set-checkpoint", leaf);
            setCheckpoint(trunk.session, leaf);
            message.metadata.decisionHeader = {
              label: "📌 Checkpoint set",
              note: "A side topic started — the main line is parked here.",
              rollbackable: true,
            };
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
        const collapsed = await collapseLiveBranch("Topic shift — collapsing the previous branch");

        // Pull a related prior branch's context onto the fresh branch (one pointer, no merge).
        let related: Awaited<ReturnType<typeof findRelatedBranch>> = null;

        if (collapsed != null) {
          related = await findRelatedBranch(
            { side: app.agent.side, log: app.log },
            { session: trunk.session, branchRecords: trunk.branchRecords, message: message.text },
          );

          if (related != null) injectRelatedBranchContext(trunk.session, related, app.log);

          // An automatic topic shift is a /rollback target (Batch 4). Manual /new (forceNew above) does
          // NOT record it — only automatic decisions are rollback targets (R7). The decision surfaces on
          // the shifted response via the turn-scoped header, which signals /rollback is available — set
          // it exactly where the decision is recorded so the two can never disagree (R8).
          if (preDecisionLeafId != null) {
            recordLastAutoDecision(trunk.session, "new", preDecisionLeafId);
            message.metadata.decisionHeader = {
              label: "🆕 New topic",
              note: "Started a fresh topic — the previous one was collapsed.",
              rollbackable: true,
            };
          }
        }

        app.log.info(
          {
            collapsedBranchId: trunk.liveBranchId,
            newBaseId: collapsed?.newBaseId ?? null,
            relatedBranchId: related?.branchId ?? null,
          },
          "topic shifted",
        );
      }

      return next();
    });
  },
});
