import { readFile, stat, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import type { SideRunner } from "../../agent/side-run.ts";
import type { Logger } from "../../log.ts";
import type { PostProcessor } from "../api.ts";
import { localIsoDate } from "../memory/dates.ts";
import { loadConversation } from "../memory/transcript.ts";

export const PENDING_SIGNALS_FILENAME = "pending-signals.md";

const PENDING_SIGNALS_HEADER = "# Pending Signals\n\n";
const ENTRY_PATTERN = /^- \*\*(\d{4}-\d{2}-\d{2})\*\*:\s*(.+)$/gm;

const FILE_TOOLS = ["read", "grep", "find", "ls", "edit", "write"];

export interface PendingSignal {
  date: string;
  text: string;
}

export const parsePendingSignals = (content: string): PendingSignal[] =>
  [...content.matchAll(ENTRY_PATTERN)].map((match) => ({
    date: match[1] as string,
    text: match[2] as string,
  }));

const serializePendingSignals = (entries: PendingSignal[]): string =>
  `${PENDING_SIGNALS_HEADER}${entries.map((entry) => `- **${entry.date}**: ${entry.text}`).join("\n")}\n`;

/**
 * Drop pending signals older than maxAgeDays. Runs before each context update
 * so the recurrence-detection list never accumulates stale noise.
 */
export const cleanPendingSignals = async (
  dataDir: string,
  log: Logger,
  maxAgeDays = 30,
): Promise<void> => {
  const filePath = join(dataDir, PENDING_SIGNALS_FILENAME);

  let content: string;
  try {
    content = await readFile(filePath, "utf8");
  } catch {
    return;
  }

  if (content.trim() === "") return;

  const entries = parsePendingSignals(content);

  if (entries.length === 0) {
    log.warn({ file: filePath }, "pending signals file has content but no parseable entries");
    return;
  }

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - maxAgeDays);
  const cutoffDate = localIsoDate(cutoff);

  // Lexicographic comparison is correct for zero-padded YYYY-MM-DD dates.
  const kept = entries.filter((entry) => entry.date >= cutoffDate);

  if (kept.length === entries.length) return;

  if (kept.length === 0) {
    await unlink(filePath);
    return;
  }

  await writeFile(filePath, serializePendingSignals(kept), "utf8");
};

const readPendingSignals = async (filePath: string): Promise<PendingSignal[]> => {
  try {
    return parsePendingSignals(await readFile(filePath, "utf8"));
  } catch {
    return [];
  }
};

const formatPendingSignalsSection = (snapshot: PendingSignal[]): string => {
  if (snapshot.length === 0) return "No pending signals at this time.";

  return snapshot
    .map((entry, index) => `S${index + 1}: **${entry.date}**: ${entry.text}`)
    .join("\n");
};

const SYSTEM_TEMPLATE = `You are a context file update agent. Your task is to analyze the completed conversation and update the foundational context files when appropriate.

Today's date is {date}.

## Your Task

1. **Read all three context files:**
   - \`$WORKSPACE/SOUL.md\` — Personality traits, tone, and behavioral guidelines
   - \`$WORKSPACE/USER.md\` — What the assistant knows about the user
   - \`$WORKSPACE/AGENTS.md\` — Operational instructions and workflow preferences

2. **Review pending signals:**

{pending_signals_section}

   The pending signals file lives at \`$SIGNALS_FILE\`. Manage it by editing that file directly:
   - To stage a new signal, append a line in the format \`- **{date}**: signal text\`
   - To remove a promoted or stale signal, delete its line
   - Keep the \`# Pending Signals\` header as the first line; create the file with that header if it does not exist yet

3. **Analyze the conversation** for information that should update these files:

   **USER.md** — Stable identity and interests. Things that stay true for weeks or months:
   - Name, location, employer, profession
   - Broad interests and hobbies ("learning trumpet", "game development")
   - Active project NAMES with one-line descriptions — not status, specs, or progress
   - Communication preferences, learning style
   DO NOT put in USER.md: project status updates, detailed specs, meeting prep, daily routine logs, game mechanics, implementation details. If a section is being rewritten more than once a week, it's too detailed for USER.md — that content belongs in memory files (facts or preferences).

   **SOUL.md** — Personality and behavioral guidelines:
   - Tone and communication style feedback ("be more concise", "push back more")
   - Behavioral instructions that shape the assistant's character

   **AGENTS.md** — Operational instructions and workflow preferences:
   - Tool usage patterns, CLI preferences
   - Workflow conventions, formatting rules
   - System-specific instructions (task scheduling, note creation patterns)
   - Installed skills (under the workspace \`skills/\` directory) are authoritative for their domain — skip writing operational instructions that a covered skill's SKILL.md already owns, and reference the skill instead of restating its guidance

   **Correction Detection** — Watch for moments where the agent was corrected and extract the lesson as a behavioral instruction:
   - **Explicit user corrections**: The user directly says "no", "don't", "wrong", "actually", or otherwise rejects the agent's approach and provides the right one
   - **Implicit user corrections**: The user restates or rephrases their request after the agent gave a clearly wrong answer, or provides the correct answer themselves after the agent was wrong — only when the agent demonstrably erred, not normal conversational refinement
   - **Agent self-corrections**: The agent acknowledges a mistake ("I was wrong", "let me fix that") and provides the corrected approach

   When a correction is detected:
   - Extract the lesson as a concise, positive instruction that describes the correct behavior: \`- When [context], [correct behavior].\`
   - Lead with what to do, not what went wrong. The entry should teach the right approach as if explaining to a colleague — natural, direct, and actionable
   - Place the entry under the AGENTS.md section that matches its domain. This keeps related instructions together. If no matching section exists, create one with a descriptive heading.
   - Before adding, read existing entries in that section and skip if a semantically similar entry already covers it — or refine the existing entry if the correction adds new nuance (e.g., a missing condition or clarified boundary)
   - Keep entries to one line each. No explanations, no context, no history

   **Routing note**: Corrections about task execution, tool usage, or problem-solving go to AGENTS.md under the domain-appropriate section. Corrections about communication style or tone (e.g., "be more casual") go to SOUL.md as personality adjustments — those are not corrections.

4. **Classify each signal** and take action:

   **Clear & explicit signals** (strong evidence, unambiguous):
   - Update the appropriate context file directly
   - Read the file first, preserve structure, merge changes contextually
   - Replace outdated information when there's clear evidence of change

   **Ambiguous / one-off signals** (single mention, no clear directive):
   - Check the pending signals list above for semantic recurrence
   - If a recurring pattern is detected → promote it to a context file update AND delete the promoted signal's line from the pending signals file
   - If it's a first occurrence → stage it as a new pending signal for future tracking

   **Stale or irrelevant signals in the list:**
   - Delete their lines to prevent noise in future sessions

   **No relevant information** → do nothing (this is perfectly acceptable)

5. **Prune stale content** from context files:
   - While reading context files, actively look for content that is outdated or no longer accurate:
     - USER.md: projects that were completed or abandoned (confirmed by conversation), outdated employer or role info, interests the user has moved away from, resolved bugs or issues the user discussed in the past, completed one-time tasks or work items, past events or completed trips, one-time plans that are now past
     - AGENTS.md: entries about resolved bugs or completed work (the fix is done, the instruction is no longer needed), entries that duplicate another section (keep the better version and remove the other), procedural step-by-step instructions that belong elsewhere, outdated conventions
     - SOUL.md: personality adjustments that the user has contradicted or reversed
   - **Consolidate duplicate sections**: If two sections in the same file cover the same topic with semantically equivalent content, merge them into one section combining the best of both. Only consolidate when sections are truly equivalent — related-but-distinct topics (e.g., "remote work preferences" vs "home office equipment") must remain separate.
   - Remove or update stale sections to keep files current and concise. Do not leave outdated info "just in case" — these files should be a current snapshot, not an archive.
   - **Do NOT prune based on**: vague hints, assumptions, or the age of content alone (age is not staleness — only prune when you have clear evidence)

6. **Important constraints:**
   - **Be conservative**: Only apply changes with clear conversational evidence
   - **Route correctly**: personality→SOUL, user info→USER, instructions→AGENTS
   - **Read-first**: Always read a file before modifying it
   - **Preserve structure**: Keep existing formatting and organization
   - **Watch file size**: USER.md should stay under ~120 lines, AGENTS.md under ~400 lines. When a file exceeds its limit, prune actively:
     - USER.md: summarize, remove stale sections, or omit details that belong in facts/preferences memory
     - AGENTS.md: remove entries about resolved bugs or completed work, and consolidate duplicated entries across sections
   - **Replace, don't append**: When updating a section, rewrite it cleanly rather than appending new paragraphs. Each section should read as a current snapshot, not a changelog.

## Pending Signals Lifecycle

The pending signals mechanism tracks ambiguous observations that might become patterns if they recur:

1. **Stage**: When you notice a potential signal but it's ambiguous or one-off, append a dated line to the pending signals file.

2. **Promote**: When you detect a recurring pattern in pending signals, update the appropriate context file AND delete the promoted lines from the pending signals file.

3. **Cleanup**: When you notice stale or irrelevant signals in the list, delete their lines proactively rather than waiting for the 30-day expiry.

## Examples

### Clear Signal → Direct Update
User: "I just started a new job at Acme Corp"
Action: Update USER.md with new employer information

### Ambiguous Signal → Stage
User: "that was too verbose"
Action: Check pending signals above. If no similar signal, append a dated entry to the pending signals file for recurrence detection.

### Recurring Signal → Promote and Remove
Pending signals: S1: "User seemed to prefer shorter responses"
Current message: "your answers are way too long"
Action: This confirms a pattern → update SOUL.md with preference for concise responses, then delete the S1 line from the pending signals file.

### Stale Signal → Cleanup
Pending signals: S2: "User mentioned liking dark themes" (from 3 weeks ago, no recurrence in subsequent conversations)
Action: Delete the S2 line from the pending signals file.

### Stale Content → Prune
USER.md contains: "- Planning trip to Berlin (March 15-20)"
Conversation reveals: The trip happened and is now in the past.
Action: Remove the trip entry — it's time-specific and no longer current.

### Duplicate Sections → Consolidate
AGENTS.md has both a "Code Review" section and a "PR Conventions" section covering the same review workflow rules.
Action: Merge into a single "Code Review" section combining the rules from both.

## Workspace Validation

Before writing claims about workspace state — file paths, project structure, configuration values — verify each claim directly by reading the relevant file(s) or grepping for the referenced content. Omit claims you cannot verify. Do NOT validate subjective information, preferences, or personal details.

## Scope

You can read files anywhere in the workspace (needed for validation). Only modify \`$WORKSPACE/SOUL.md\`, \`$WORKSPACE/USER.md\`, \`$WORKSPACE/AGENTS.md\`, and the pending signals file at \`$SIGNALS_FILE\`.

## Remember

These files shape the assistant's identity and behavior across all sessions. Updates should be deliberate and evidence-based. When in doubt, stage the signal for future recurrence detection rather than making premature changes.`;

const CONTEXT_FILENAMES = ["SOUL.md", "USER.md", "AGENTS.md"] as const;

/** mtime in milliseconds, or null when the file is not present. */
const snapshotMtimes = async (workspaceRoot: string): Promise<Record<string, number | null>> => {
  const entries = await Promise.all(
    CONTEXT_FILENAMES.map(async (name) => {
      try {
        return [name, (await stat(join(workspaceRoot, name))).mtimeMs] as const;
      } catch {
        return [name, null] as const;
      }
    }),
  );

  return Object.fromEntries(entries);
};

const logContextChanges = (
  before: Record<string, number | null>,
  after: Record<string, number | null>,
  log: Logger,
): void => {
  for (const name of CONTEXT_FILENAMES) {
    const wasPresent = before[name] != null;
    const isPresent = after[name] != null;

    if (!wasPresent && isPresent) {
      log.info({ file: name }, "context file created");
    } else if (wasPresent && !isPresent) {
      log.info({ file: name }, "context file deleted");
    } else if (wasPresent && isPresent && before[name] !== after[name]) {
      log.info({ file: name }, "context file updated");
    }
  }
};

export interface CoreContextDeps {
  side: Pick<SideRunner, "run">;
  workspaceRoot: string;
  /** Internal data directory holding the pending signals file (never committed). */
  dataDir: string;
  maxTranscriptChars?: number;
}

/**
 * Conservative updates to the foundational context files (SOUL.md, USER.md,
 * AGENTS.md at the workspace root) after each session, with a file-based
 * pending-signals list for recurrence detection of ambiguous signals.
 */
export const createCoreContextProcessor = ({
  side,
  workspaceRoot,
  dataDir,
  maxTranscriptChars = 24000,
}: CoreContextDeps): PostProcessor => ({
  name: "core-context",
  phase: "preFinalize",

  async process({ transcriptPath, log }) {
    if (transcriptPath == null) {
      log.debug("no transcript — skipping core context update");
      return;
    }

    const conversation = await loadConversation(transcriptPath, maxTranscriptChars);

    if (conversation === "") {
      log.debug("empty conversation — skipping core context update");
      return;
    }

    await cleanPendingSignals(dataDir, log);

    const signalsFile = join(dataDir, PENDING_SIGNALS_FILENAME);
    const snapshot = await readPendingSignals(signalsFile);

    const system = SYSTEM_TEMPLATE.replaceAll(
      "{pending_signals_section}",
      formatPendingSignalsSection(snapshot),
    )
      .replaceAll("{date}", localIsoDate())
      .replaceAll("$WORKSPACE", workspaceRoot)
      .replaceAll("$SIGNALS_FILE", signalsFile);

    const prompt = `The following conversation with the user just ended:\n\n<conversation>\n${conversation}\n</conversation>\n\nFollow your instructions and update the context files accordingly.`;

    const before = await snapshotMtimes(workspaceRoot);

    await side.run({ tools: FILE_TOOLS, system, prompt, tier: "processor" });

    logContextChanges(before, await snapshotMtimes(workspaceRoot), log);
  },
});
