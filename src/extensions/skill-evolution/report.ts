import {
  DISPATCH_BACKGROUND_TASK_EVENT,
  type DispatchBackgroundTaskPayload,
} from "../../events.ts";
import { impactLogPath } from "./layout.ts";
import type { ReportedProposal } from "./propose.ts";
import type { ImpactLogEntry } from "./store.ts";

/**
 * The reporter (S9, R10): when a run verified at least one proposal, dispatch an ad-hoc background
 * task seeded with the run's context through the neutral app-event contract. The tasks extension
 * subscribes, validates, and creates the pending instance — the existing 60 s tick executes it, so
 * this side needs zero execution machinery (DES-002: the event is the only cross-extension surface).
 */

/** The `source` every skill-evolution dispatch carries — the subscriber's log field. */
export const REPORT_SOURCE = "skill-evolution";

/**
 * The default post-work prompt (R10): notification-only — it generates one notification for the
 * user and deliberately takes no further action. A configured `postWorkPrompt` replaces it.
 */
export const DEFAULT_POST_WORK_PROMPT = [
  "The skill-evolution pass just created the skill-change proposals listed below.",
  "Generate one notification for the user summarizing them, then stop.",
  "Take no further action: do not open pull requests, do not merge, do not edit the skills.",
].join(" ");

export interface ReportRunInput {
  /** The app event bus emit — the dispatch event is fire-and-forget. */
  emit: (event: string, payload: unknown) => void;
  workspaceRoot: string;
  /** Rows the host verified this run — the caller invokes the reporter only when at least one exists. */
  verified: readonly ImpactLogEntry[];
  /** The run's full reported proposals — each verified row's reasoning, paired by branch. */
  reported: readonly ReportedProposal[];
  /** The configured post-work prompt; the default above is notification-only. */
  postWorkPrompt?: string;
}

/** Inline prose for the single-line reasoning fields — a newline can never restructure the block. */
const inlineText = (text: string): string => text.replaceAll(/\r?\n/g, " ").trim();

/**
 * One proposal's evidence as review-ready bullets: the agent's dated lines re-normalized as list
 * items (marker and heading sigils strip in both orders, lines that normalize to nothing drop,
 * survivors gain `- `) so free-form markdown can never render as a heading and restructure the
 * surrounding context section.
 */
const evidenceBullets = (evidence: string): string[] =>
  evidence
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((line) =>
      line
        .replace(/^[-*+]\s*/, "")
        .replace(/^#+\s*/, "")
        .replace(/^[-*+]\s*/, "")
        .trim(),
    )
    .filter((line) => line !== "")
    .map((line) => `- ${line}`);

/**
 * One verified proposal as a review-ready block: the row's git facts (skill, pattern, tip) under
 * the branch heading, then the reported reasoning. A verified branch missing from the reported
 * list — impossible in production wiring (rows are built from reports) — degrades to the plain
 * one-line shape rather than rendering reasoning the run cannot vouch for.
 */
const proposalBlock = (row: ImpactLogEntry, proposal: ReportedProposal | undefined): string => {
  // Every agent-authored field renders through `inlineText` — `branch` is pattern-validated and
  // `tip` is a git hex sha, but skill/pattern/description arrive as free-form reported strings.
  if (proposal == null) {
    return `- \`${row.branch}\` (${inlineText(row.skill)}, ${inlineText(row.pattern)}): ${inlineText(
      row.description,
    )} — tip ${row.tip}`;
  }

  return [
    `### \`${row.branch}\``,
    "",
    `- Skill: ${inlineText(row.skill)} — pattern: ${inlineText(row.pattern)} — tip: ${row.tip}`,
    `- What it does: ${inlineText(row.description)}`,
    `- Problem: ${inlineText(proposal.problem)}`,
    `- Root cause: ${inlineText(proposal.rootCause)}`,
    "- Evidence:",
    ...evidenceBullets(proposal.evidence),
  ].join("\n");
};

/**
 * The run summary inlined into every dispatch: counts, patterns, ledger path, then one
 * review-ready block per verified proposal. The framing sentence over the blocks is
 * informational — what the material is, never what to do with it — so the same rendering stays
 * coherent under the notification-only default prompt (which forbids opening PRs) and under any
 * configured acting prompt.
 */
const runContextSection = (
  workspaceRoot: string,
  verified: readonly ImpactLogEntry[],
  reported: readonly ReportedProposal[],
): string => {
  const patterns = [...new Set(verified.map((row) => row.pattern))];
  // First report of a duplicated branch wins — verification records the first one too, so a
  // block's git facts and its reasoning always come from the same reported entry.
  const byBranch = new Map<string, ReportedProposal>();
  for (const proposal of reported) {
    if (!byBranch.has(proposal.branch)) {
      byBranch.set(proposal.branch, proposal);
    }
  }

  return [
    "## Run context",
    "",
    `- Proposals created: ${verified.length}`,
    `- Patterns touched: ${patterns.map(inlineText).join(", ")}`,
    `- Impact log: ${impactLogPath(workspaceRoot)}`,
    "",
    "Verified proposals — each block carries the proposal's full reasoning (what the change does, the problem, the root cause, the evidence): the material a pull-request body should carry when a proposal becomes one.",
    "",
    verified.map((row) => proposalBlock(row, byBranch.get(row.branch))).join("\n\n"),
  ].join("\n");
};

/**
 * Emit the dispatch event. The prompt is the configured `postWorkPrompt` (verbatim) or the
 * notification-only default, followed by the run context. The explicit `goal` skips the background
 * runner's goal-extraction call — it names the follow-up in the prompt-independent terms both the
 * default (notify) and a configured (act) prompt share.
 */
export const reportRun = ({
  emit,
  workspaceRoot,
  verified,
  reported,
  postWorkPrompt,
}: ReportRunInput): void => {
  const payload: DispatchBackgroundTaskPayload = {
    prompt: `${postWorkPrompt ?? DEFAULT_POST_WORK_PROMPT}\n\n${runContextSection(
      workspaceRoot,
      verified,
      reported,
    )}`,
    goal: `Follow up on the ${verified.length} skill-evolution proposal${
      verified.length === 1 ? "" : "s"
    } created tonight (${verified.map((row) => row.branch).join(", ")})`,
    source: REPORT_SOURCE,
  };

  emit(DISPATCH_BACKGROUND_TASK_EVENT, payload);
};
