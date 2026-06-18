import type { AgentSession } from "@earendil-works/pi-coding-agent";

import {
  type BranchSummaryDetails,
  branchEntriesSinceBase,
  branchWithSummary,
  getLeafId,
  messageText,
} from "../../agent/session-tree.ts";
import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import { BRANCH_SUMMARY, writeBoomerangState } from "../../sessions/trunk.ts";

/**
 * Branch collapse. On a topic shift the current branch is summarized and recorded on
 * the trunk as a `branch_summary` entry; the abandoned branch survives in the append-only tree,
 * reachable through the `originalLeafId` stored in the entry's details. Collapse degrades gracefully
 * (R11): on any failure it logs and returns null so the caller proceeds on the current branch with no
 * content lost.
 */

export interface CollapseDeps {
  side: Pick<SideRunner, "complete">;
  log: Logger;
}

export interface CollapseArgs {
  session: AgentSession;
  /** The base (current trunk tip) the live branch extends; the branch's own turns start after it. */
  currentBaseId: string | null;
  /** Deterministic `topic-N` id for the collapsing branch. */
  branchId: string;
  reason?: string;
  lastExchange?: string | null;
}

const SUMMARY_SYSTEM = [
  "You summarize a coding-assistant conversation branch so the assistant can continue from a compact",
  "trunk later. Capture, concisely but completely:",
  "- the user's goal,",
  "- key decisions made,",
  "- completed work,",
  "- changed files or commands,",
  "- open questions,",
  "- next steps.",
  "Output only the summary prose, no preamble.",
].join("\n");

/** The branch's own turns: entries on the leaf path strictly after `currentBaseId`. */
const renderBranchTranscript = (session: AgentSession, currentBaseId: string | null): string => {
  const sections: string[] = [];

  for (const entry of branchEntriesSinceBase(session, currentBaseId)) {
    if (entry.type !== "message") continue;

    const role = entry.message.role;
    if (role !== "user" && role !== "assistant") continue;

    const text = messageText(entry);
    if (text === "") continue;

    sections.push(`${role === "user" ? "User" : "Assistant"}: ${text}`);
  }

  return sections.join("\n\n");
};

export const collapseCurrentTopic = async (
  deps: CollapseDeps,
  args: CollapseArgs,
): Promise<{ newBaseId: string } | null> => {
  try {
    const originalLeafId = getLeafId(args.session);

    if (originalLeafId == null) {
      deps.log.warn({ branchId: args.branchId }, "collapse skipped — session has no leaf");
      return null;
    }

    const summary = await deps.side.complete({
      tier: "processor",
      system: SUMMARY_SYSTEM,
      user: renderBranchTranscript(args.session, args.currentBaseId),
    });

    const details: BranchSummaryDetails = {
      customType: BRANCH_SUMMARY,
      branchId: args.branchId,
      originalLeafId,
      baseId: args.currentBaseId,
      reason: args.reason,
      lastExchange: args.lastExchange,
    };

    const newBaseId = branchWithSummary(args.session, args.currentBaseId, summary, details);

    writeBoomerangState(args.session, {
      currentTopicBaseId: newBaseId,
      lastDecision: "shift",
      relatedBranchId: null,
    });

    deps.log.info(
      { branchId: args.branchId, newBaseId, originalLeafId, summaryLen: summary.length },
      "branch collapsed",
    );

    return { newBaseId };
  } catch (error) {
    deps.log.error(
      { err: error, branchId: args.branchId },
      "branch collapse failed — proceeding on current branch",
    );
    return null;
  }
};
