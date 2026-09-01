import type { ReportedProposal } from "../../src/extensions/skill-evolution/propose.ts";

/**
 * The shared reported-proposal fixture — the agent's seven-field self-report for the
 * canonical deploy/--env proposal. Pass `over` to vary any field (blank-field matrices,
 * pairing by branch, hostile-markdown cases).
 */
export const proposalFixture = (
  branch: string,
  over: Partial<ReportedProposal> = {},
): ReportedProposal => ({
  branch,
  skill: "deploy",
  pattern: "deploy-env-flag.md",
  description: "Add the --env flag to the deploy guidance",
  problem: "Deploys fail on the --env flag the skill omits",
  rootCause: "The deploy guidance predates the --env requirement",
  evidence: "- 2027-03-01 (topic-2): deploy failed with unknown flag --env",
  ...over,
});
