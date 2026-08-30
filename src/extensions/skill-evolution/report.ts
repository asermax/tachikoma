import {
  DISPATCH_BACKGROUND_TASK_EVENT,
  type DispatchBackgroundTaskPayload,
} from "../../events.ts";
import { impactLogPath } from "./layout.ts";
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
  "The skill-evolution pass just created the skill-modification proposals listed below.",
  "Generate one notification for the user summarizing them, then stop.",
  "Take no further action: do not open pull requests, do not merge, do not edit the skills.",
].join(" ");

export interface ReportRunInput {
  /** The app event bus emit — the dispatch event is fire-and-forget. */
  emit: (event: string, payload: unknown) => void;
  workspaceRoot: string;
  /** Rows the host verified this run — the caller invokes the reporter only when at least one exists. */
  verified: readonly ImpactLogEntry[];
  /** The configured post-work prompt; the default above is notification-only. */
  postWorkPrompt?: string;
}

/** The run summary inlined into every dispatch: counts, patterns, ledger path, the proposals. */
const runContextSection = (workspaceRoot: string, verified: readonly ImpactLogEntry[]): string => {
  const patterns = [...new Set(verified.map((row) => row.pattern))];

  return [
    "## Run context",
    "",
    `- Proposals created: ${verified.length}`,
    `- Patterns touched: ${patterns.join(", ")}`,
    `- Impact log: ${impactLogPath(workspaceRoot)}`,
    "",
    "Verified proposals:",
    "",
    ...verified.map(
      (row) =>
        `- \`${row.branch}\` (${row.skill}, ${row.pattern}): ${row.description} — tip ${row.tip}`,
    ),
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
  postWorkPrompt,
}: ReportRunInput): void => {
  const payload: DispatchBackgroundTaskPayload = {
    prompt: `${postWorkPrompt ?? DEFAULT_POST_WORK_PROMPT}\n\n${runContextSection(
      workspaceRoot,
      verified,
    )}`,
    goal: `Follow up on the ${verified.length} skill-evolution proposal${
      verified.length === 1 ? "" : "s"
    } created tonight (${verified.map((row) => row.branch).join(", ")})`,
    source: REPORT_SOURCE,
  };

  emit(DISPATCH_BACKGROUND_TASK_EVENT, payload);
};
