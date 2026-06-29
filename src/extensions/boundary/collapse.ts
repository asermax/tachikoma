import type { AgentSession } from "@earendil-works/pi-coding-agent";

import {
  type BranchCollapseArgs,
  type BranchCollapseDeps,
  collapseLiveTopicBranch,
  renderBranchTranscript,
  SUMMARY_SYSTEM,
} from "../../agent/branch-collapse.ts";
import { collapseTangent, getLeafId } from "../../agent/session-tree.ts";
import {
  clearCheckpoint,
  nextTangentId,
  readBoomerangState,
  writeBoomerangState,
} from "../../sessions/trunk.ts";

/**
 * Branch collapse. On a topic shift the current branch is summarized and recorded on the trunk as a
 * `branch_summary` entry; the abandoned branch survives in the append-only tree, reachable through the
 * `originalLeafId` stored in the entry's details. The generic summarize-and-collapse lives in core
 * (`agent/branch-collapse.ts`, shared with the trunk-close path); this module layers the topic-shift
 * boomerang snapshot on top of it and owns the tangent summarize-to-checkpoint. Collapse degrades
 * gracefully (R11): on any failure it logs and returns null so the caller proceeds on the current branch
 * with no content lost.
 */

export type CollapseDeps = BranchCollapseDeps;
export type CollapseArgs = BranchCollapseArgs;

export interface TangentSummaryArgs {
  session: AgentSession;
  /** The active checkpoint's main-line tip entry id; the tangent folds into a summary rooted here. */
  checkpointId: string;
}

export const collapseCurrentTopic = async (
  deps: CollapseDeps,
  args: CollapseArgs,
): Promise<{ newBaseId: string } | null> => {
  const result = await collapseLiveTopicBranch(deps, args);
  if (result == null) return null;

  // A topic collapse invalidates any active checkpoint — the main-line point it marked is gone — so
  // checkpointId clears (R2). The auto-decision log is written separately by the decision path and is
  // preserved here (not reset by a shift), so a prior automatic decision stays a rollback target.
  const priorAutoDecision = readBoomerangState(args.session)?.lastAutoDecision ?? null;

  writeBoomerangState(args.session, {
    currentTopicBaseId: result.newBaseId,
    lastDecision: "shift",
    relatedBranchId: null,
    checkpointId: null,
    lastAutoDecision: priorAutoDecision,
  });

  return result;
};

/**
 * Summarize the tangent taken since `checkpointId` back into the checkpoint (R3/R4/R5): generate a
 * tangent summary, collapse the branch rooted at the checkpoint via {@link collapseTangent} (the leaf
 * re-seats onto the summary so the main line resumes at the checkpoint — only the tangent is parked
 * away, never the main line), and clear the checkpoint. The summary is marked `kind: "tangent"` so it
 * is excluded from `getBranchRecords`/`ask_branch`/extraction. Shares the topic collapse's summary
 * prompt and transcript rendering. Degrades gracefully (R11): on any failure it logs and returns null,
 * leaving the checkpoint active.
 */
export const summarizeCurrentTangent = async (
  deps: CollapseDeps,
  args: TangentSummaryArgs,
): Promise<{ newBaseId: string } | null> => {
  try {
    const originalLeafId = getLeafId(args.session);

    if (originalLeafId == null) {
      deps.log.warn(
        { checkpointId: args.checkpointId },
        "tangent summarize skipped — session has no leaf",
      );
      return null;
    }

    const summary = await deps.side.complete({
      tier: "processor",
      system: SUMMARY_SYSTEM,
      // The tangent's own turns: entries on the leaf path strictly after the checkpoint.
      user: renderBranchTranscript(args.session, args.checkpointId),
    });

    const tangentId = nextTangentId(args.session);
    const newBaseId = collapseTangent(args.session, args.checkpointId, summary, {
      tangentId,
      originalLeafId,
    });

    clearCheckpoint(args.session);

    deps.log.info(
      {
        tangentId,
        checkpointId: args.checkpointId,
        newBaseId,
        originalLeafId,
        summaryLen: summary.length,
      },
      "tangent summarized to checkpoint",
    );

    return { newBaseId };
  } catch (error) {
    deps.log.error(
      { err: error, checkpointId: args.checkpointId },
      "tangent summarize failed — checkpoint left active",
    );
    return null;
  }
};
