import { rm } from "node:fs/promises";

import type { AgentSession, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";
import { createBranchFile } from "../../agent/session-tree.ts";
import type { Logger } from "../../log.ts";
import { getBranchRecords, readBoomerangState } from "../../sessions/trunk.ts";
import type { AgentApi } from "../api.ts";

/**
 * `ask_branch` tool. Answers a focused question from a prior branch's FULL conversation
 * (not its summary) by headless-forking that branch's `originalLeafId`. Rejects a request targeting
 * the active (live) branch — it is already in context — and reports gracefully (no turn failure) for
 * an unknown branch id.
 */

export const AskBranchParams = Type.Object({
  branchId: Type.String({
    description: "The branch identifier to query, e.g. topic-3.",
  }),
  question: Type.String({
    description: "Focused question about context from that previous branch.",
  }),
});

export interface AskBranchDeps {
  /** The live trunk session, or null when no trunk is active yet. */
  getTrunkSession: () => AgentSession | null;
  shadowFork: AgentApi["shadowFork"];
  log: Logger;
}

const textResult = (text: string) => ({
  content: [{ type: "text" as const, text }],
  details: undefined,
});

export const handleAskBranch = async (
  deps: AskBranchDeps,
  args: Static<typeof AskBranchParams>,
): Promise<string> => {
  const trunk = deps.getTrunkSession();

  if (trunk == null) {
    return `No such branch '${args.branchId}' — no conversation trunk is active.`;
  }

  const records = getBranchRecords(trunk);
  const record = records.find((entry) => entry.branchId === args.branchId);

  if (record == null) {
    return `No such branch '${args.branchId}' exists.`;
  }

  // The base of the live branch is the latest collapse's summary id; a request targeting it is asking
  // about the branch already live in context, so reject rather than fork.
  const currentBase = readBoomerangState(trunk)?.currentTopicBaseId ?? null;

  if (record.summaryEntryId === currentBase) {
    return `Branch '${args.branchId}' is the currently active branch — it is already in context.`;
  }

  const branchFile = createBranchFile(trunk, record.originalLeafId);

  if (branchFile == null) {
    return `Could not open branch '${args.branchId}' for lookup.`;
  }

  const fork = await deps.shadowFork(branchFile, { tier: "searcher" });

  try {
    const answer = await fork.prompt(
      [
        `Answer using only this branch's conversation. If the answer is not present here, say the`,
        `branch does not contain enough information. Be concise but include concrete details.`,
        "",
        "<question>",
        args.question,
        "</question>",
      ].join("\n"),
    );

    return answer.trim() !== ""
      ? answer.trim()
      : `Branch '${args.branchId}' did not return any context.`;
  } finally {
    await fork.dispose().catch((error) => {
      deps.log.warn({ err: error }, "ask_branch shadow-fork dispose failed");
    });
    await rm(branchFile, { force: true });
  }
};

export const createAskBranchFactory =
  (deps: AskBranchDeps): ExtensionFactory =>
  (pi) => {
    pi.registerTool({
      name: "ask_branch",
      label: "Ask Previous Branch",
      description:
        "Ask a previous topic branch of today's conversation for context when a returned topic needs details not present in the current trunk context. Answered from that branch's full original conversation, not its summary.",
      promptSnippet: "Ask a previous topic branch for missing context by branch id",
      promptGuidelines: [
        "Use ask_branch when hidden related-branch context names a previous branch id and the user asks about details from that branch that are not in the current context.",
      ],
      parameters: AskBranchParams,
      async execute(_toolCallId, params) {
        return textResult(await handleAskBranch(deps, params));
      },
    });
  };
