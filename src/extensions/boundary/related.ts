import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";

import { appendInContextEntry, getEntry } from "../../agent/session-tree.ts";
import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { BranchRecord } from "../../sessions/trunk.ts";

/**
 * Related-branch matcher + pointer injection. After a collapse, an LLM matcher compares
 * the new message against prior branch summaries (no vector DB); on a match a single pointer is
 * injected as hidden in-context content — the summary is already on the trunk, so the pointer adds
 * only its last exchange and a hint to use `ask_branch` for more. Branches are never merged. The
 * matcher fails soft to null so a failure simply injects nothing.
 */

export const RELATED_BRANCH_CUSTOM_TYPE = "tachikoma-related-branch";

const BranchMatchSchema = Type.Object({
  branchId: Type.Union([Type.String(), Type.Null()]),
  reason: Type.String(),
});

type BranchMatch = Static<typeof BranchMatchSchema>;

export interface RelatedDeps {
  side: Pick<SideRunner, "classify">;
  log: Logger;
}

export interface FindRelatedArgs {
  session: AgentSession;
  branchRecords: BranchRecord[];
  message: string;
}

const summaryTextOf = (session: AgentSession, record: BranchRecord): string => {
  const entry = getEntry(session, record.summaryEntryId);

  return entry != null && entry.type === "branch_summary" ? entry.summary : "";
};

const MATCH_SYSTEM = [
  "A new user message has started a fresh topic branch from the daily trunk.",
  "Decide whether it clearly continues one of the previous branch summaries below.",
  "Return a match only when the new message is clearly about the same topic, project, goal, or",
  "unresolved thread. Return null for ambiguous messages or genuinely new topics.",
  "Never invent a branch id; only pick one from the listed branches.",
].join("\n");

const renderRecords = (session: AgentSession, records: BranchRecord[]): string =>
  records
    .map((record) => [`Branch id: ${record.branchId}`, summaryTextOf(session, record)].join("\n"))
    .join("\n\n---\n\n");

export const findRelatedBranch = async (
  deps: RelatedDeps,
  args: FindRelatedArgs,
): Promise<BranchRecord | null> => {
  if (args.branchRecords.length === 0) return null;

  try {
    const result: BranchMatch = await deps.side.classify({
      tier: "classifier",
      schema: BranchMatchSchema,
      system: MATCH_SYSTEM,
      user: [
        "<candidate_user_message>",
        args.message,
        "</candidate_user_message>",
        "",
        "<previous_branch_summaries>",
        renderRecords(args.session, args.branchRecords),
        "</previous_branch_summaries>",
      ].join("\n"),
    });

    if (result.branchId == null) return null;

    return args.branchRecords.find((record) => record.branchId === result.branchId) ?? null;
  } catch (error) {
    deps.log.error({ err: error }, "related-branch match failed — injecting no pointer");
    return null;
  }
};

const buildPointer = (record: BranchRecord, summaryText: string): string =>
  [
    `This new branch may continue previous branch ${record.branchId}.`,
    "Its summary is already on the trunk; do not repeat it unless needed.",
    "Last exchange from that prior branch:",
    "",
    record.lastExchange ?? "(no last exchange captured)",
    "",
    `If you need details from ${record.branchId} that are not in the visible context, call ask_branch`,
    `with branchId "${record.branchId}" and a focused question.`,
    summaryText !== "" ? `\nBranch summary:\n${summaryText}` : "",
  ].join("\n");

export const injectRelatedBranchContext = (session: AgentSession, record: BranchRecord): void => {
  appendInContextEntry(
    session,
    RELATED_BRANCH_CUSTOM_TYPE,
    buildPointer(record, summaryTextOf(session, record)),
    { branchId: record.branchId },
  );
};
