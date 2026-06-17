import { Type } from "typebox";

import { defineExtension } from "../api.ts";
import { createAskBranchFactory } from "./ask-branch.ts";
import { classifyShift } from "./classifier.ts";
import { collapseCurrentTopic } from "./collapse.ts";
import { findRelatedBranch, injectRelatedBranchContext } from "./related.ts";

interface BoundaryConfig {
  enabled: boolean;
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
  }),

  setup(app) {
    const detectionEnabled = app.extensionConfig.enabled;

    if (!detectionEnabled) {
      app.log.info("boundary detection disabled by configuration");
    }

    // `ask_branch` resolves its target on the live trunk session the coordinator owns.
    app.agent.use(
      createAskBranchFactory({
        getTrunkSession: () => app.sessions.activeTrunkSession(),
        shadowFork: app.agent.shadowFork,
        log: app.log,
      }),
      { sessionScopes: ["main"] },
    );

    app.inbound.use(async (message, context, next) => {
      const trunk = context.trunk;

      // System-origin injections (session tasks, notices) and turns with no live trunk never shift
      // topics — they append to the current branch with detection skipped (R13).
      if (message.metadata.boundary === "skip" || trunk == null) return next();

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
        },
      );

      app.log.debug(
        { branchId: trunk.liveBranchId, decision, messageLen: message.text.length },
        "topic-shift classification",
      );

      if (decision === "shift") {
        const collapsed = await collapseLiveBranch("Topic shift — collapsing the previous branch");

        // Pull a related prior branch's context onto the fresh branch (one pointer, no merge).
        let related: Awaited<ReturnType<typeof findRelatedBranch>> = null;

        if (collapsed != null) {
          related = await findRelatedBranch(
            { side: app.agent.side, log: app.log },
            { session: trunk.session, branchRecords: trunk.branchRecords, message: message.text },
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
      }

      return next();
    });
  },
});
