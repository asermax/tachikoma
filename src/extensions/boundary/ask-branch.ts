import { rm } from "node:fs/promises";

import type { AgentSession, ExtensionFactory } from "@earendil-works/pi-coding-agent";
import { type Static, Type } from "typebox";
import type { Logger } from "../../log.ts";
import { getBranchRecords } from "../../sessions/trunk.ts";
import type { AgentApi } from "../api.ts";

/**
 * `ask_branch` tool. Answers a focused question from a prior branch's FULL conversation
 * (not its summary) by headless-forking that branch's `originalLeafId`. Every collapsed branch is
 * queryable — only its summary, not its full conversation, is in context — including the most recently
 * collapsed branch whose summary is the live base. Reports gracefully (no turn failure) for an unknown
 * branch id (the live, un-collapsed branch has no record yet, so it is reported as unknown).
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
  branchFile: AgentApi["branchFile"];
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
    deps.log.debug({ branchId: args.branchId }, "ask_branch unresolved — unknown branch");
    return `No such branch '${args.branchId}' exists.`;
  }

  // Cut the branch from a fresh-loaded manager (never the live trunk) so answering a lookup does not
  // repoint the live session at the branch file mid-conversation (see AgentManager.branchFile).
  const branchFile =
    trunk.sessionFile != null
      ? deps.branchFile(trunk.sessionFile, record.originalLeafId)
      : undefined;

  if (branchFile == null) {
    deps.log.debug({ branchId: args.branchId }, "ask_branch unresolved — branch file unavailable");
    return `Could not open branch '${args.branchId}' for lookup.`;
  }

  let fork: Awaited<ReturnType<AgentApi["shadowFork"]>>;

  try {
    fork = await deps.shadowFork(branchFile, { tier: "searcher" });
  } catch (error) {
    deps.log.warn({ err: error, branchId: args.branchId }, "ask_branch fork failed");
    await rm(branchFile, { force: true });

    return `Could not open branch '${args.branchId}' for lookup.`;
  }

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
        "Recover missing context from a previous topic branch of today's conversation. Answers come from that branch's FULL original conversation (its abandoned leaf), not its summary — so details that were never summarized are still reachable. Branch ids are topic-1, topic-2, ... in the order branches started; if unsure which, try the likely candidate — an unknown id reports gracefully and a branch without the answer says so.",
      promptSnippet:
        "Recover missing context from a previous topic branch by asking it a focused question",
      promptGuidelines: [
        "Reach for ask_branch whenever context seems missing and might live in a prior branch: a reaction or reply targeting a message you can't identify, the user referencing something from 'earlier' or 'that other topic', or a topic that may belong to another branch.",
        "Don't wait for the related-branch pointer to name a branch id before using it — reach for it proactively.",
      ],
      parameters: AskBranchParams,
      async execute(_toolCallId, params) {
        return textResult(await handleAskBranch(deps, params));
      },
    });
  };
