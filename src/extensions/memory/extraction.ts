import type { SideRunner } from "../../agent/side-run.ts";
import { localIsoDate } from "../../util/dates.ts";
import type { ExtractionStore, MemoryStore } from "./layout.ts";
import {
  CONTEXT_DEDUP_SECTION,
  INDEX_UPDATE_SECTION,
  STORE_PURPOSE_SECTION,
  scopeSection,
  WORKSPACE_VALIDATION_SECTION,
} from "./prompts.ts";

/** Used by the maintenance ticks and the trunk-close pipeline, which run bare headless side-runs. */
export type Runner = Pick<SideRunner, "run">;

const EPISODIC_BASE_PROMPT = `We just finished the conversation above. Using what you already know from it, create or update the episodic memory file for today.

Today's date is {date}.

## Instructions

1. **Read existing files** in \`$WORKSPACE/memories/episodic/\` to see what's there.

2. Analyze the conversation for meaningful events, discussions, and activities.

3. **Write to exactly one file per day: \`{date}.md\`.**
   - The ONLY valid filename is the date itself. No suffixes, no variants. \`{date}.md\` is correct. \`{date}-consolidated.md\`, \`{date}-final.md\`, \`{date}-updated.md\` are ALL WRONG — never create files like these.
   - If \`{date}.md\` already exists, READ it first, then EDIT it to merge the new information into the existing content. Do not create a second file.
   - If it does not exist, create \`{date}.md\`.

4. **Keep entries short and scannable:**
   - One heading per session or topic, not per conversational turn
   - 2-5 bullet points per heading capturing key outcomes
   - Target: 30-80 lines per day, even for busy days with many sessions
   - DO NOT include: verbatim quotes, step-by-step technical details, full lists of files changed, implementation specifics, or routine activity status

5. **Cleanup duty**: If you see files that don't match the \`YYYY-MM-DD.md\` pattern (e.g., files with \`-consolidated\`, \`-final\`, \`-updated\` suffixes), merge any useful content into the correct \`YYYY-MM-DD.md\` file and empty the variant so it gets cleaned up. This corrects filename format violations — episodic entries are never deleted based on their content.

6. **Important constraints**:
   - Only create or modify files within \`$WORKSPACE/memories/episodic/\`
   - If the conversation was trivial or contained no meaningful information, it is perfectly acceptable to create no files

Remember: These memories help the assistant maintain context across sessions. Focus on what would be useful to remember, not a transcript of what happened.`;

const TOPICS_BASE_PROMPT = `We just finished the conversation above. Using what you already know from it, extract or update everything worth remembering about the subjects that came up — both the stable reference facts and the user's subjective preferences — so future conversations start informed.

## Instructions

1. **Read existing files** in \`$WORKSPACE/memories/topics/\` to see what is already stored about each subject.

2. Analyze the conversation for BOTH kinds of durable signal, and fold each into the topic it concerns:

   **Stable reference information** — things that stay true across conversations:
   - Personal details about the user (job, location, family, contacts)
   - Important dates, deadlines, or upcoming events
   - Stable routines or commitments (structure only, not daily logs)
   - Key people and their roles/relationships
   - Technical decisions, configurations, or architecture that affect future work
   - Account info, service subscriptions, tool setups

   **Subjective preferences** — how the user wants things done, with their reasoning:
   - How they like things done (communication style, workflows, formats)
   - Approaches they prefer or want to avoid
   - Tool, framework, or methodology preferences
   - Scheduling or organizational preferences

   A single subject often yields both (a project's architecture is reference information; how the user wants work on it sequenced is a preference). Record both in the SAME topic file — they belong together. There is no separate store for facts versus preferences, so never choose between them: identify the subject and consolidate everything known about it into that topic.

   DO NOT store as topics:
   - Daily activity logs or status updates (that's episodic memory)
   - One-time events: bug fixes, security incidents, feature completions, outages, deployment events — these happened once on a specific date and belong in episodic memory, not here
   - Full design documents, specs, or game mechanics (project files)

3. **Before creating a new file**, search for existing overlap:
   - Use the grep tool to search existing files for the key subject or keywords
   - If an existing file covers the same subject, UPDATE that file instead of creating a new one
   - If information is spread across multiple files about the same subject, MERGE them into one file and delete the others

4. **File Consolidation at Write Time**:
   Before creating any new file, follow this mandatory sequence:
   - First, list the target directory \`$WORKSPACE/memories/topics/\` with the ls tool.
   - Identify which existing file (if any) covers the broadest subject that encompasses the new information. Match by project name, system name, tool, or domain — not by incident, date, or specific event.
   - If a broad-topic file exists, UPDATE that file. Do NOT create an incident- or date-specific sibling alongside it (e.g., if \`<project>.md\` exists, do not also create \`<project>-<bug-description>-<YYYY-MM-DD>.md\`).
   - If multiple existing files cover overlapping aspects of the same subject, prefer the most specific existing file that still covers the new information broadly — and consider merging the narrower ones into it.
   - Only create a new file when NO existing file covers the subject. When you do create one, choose a broad topic name that future related extracts can merge into — \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, \`<domain>.md\` — never a name scoped to one incident, bug, patch, or date.
   - Positive examples (broad, future-mergeable): \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, \`<domain>.md\`, \`work-info.md\`, \`tech-stack.md\`, \`communication-style.md\`.
   - Negative examples (forbidden — too narrow): \`<project>-<bug-description>-<YYYY-MM-DD>.md\`, \`<project>-patch-<issue-id>.md\`, \`<system>-incident-<date>.md\`, \`<topic>-session-<date>.md\`.

5. Manage the topic files:
   - Create new files with descriptive names ONLY when no existing file covers the subject
   - Update existing files when new information extends what is there
   - When updating a file, READ it first. If a section already covers what you're about to add, update that section rather than appending a duplicate
   - **Merge** files that overlap in subject — combine into one, delete the rest
   - **Delete** files that are outdated, redundant, or better covered elsewhere

6. **Prune stale and redundant entries**:
   - After reading existing files, actively look for entries that may be outdated or no longer accurate based on the conversation:
     - Information contradicted by new statements (e.g., file says "works at Company A" but conversation reveals a move to Company B)
     - Reversed preferences (e.g., file says "prefers dark mode" but the user now says "I switched to light mode")
     - References to completed projects, past roles, or expired commitments that the conversation confirms are done
     - Entries about tools, services, or setups the user no longer uses
   - When you find stale entries: update them if new information replaces the old, or delete the file if the entire subject is no longer relevant
   - **Do NOT prune based on**: vague hints ("I might switch..."), old dates alone (age is not staleness), or assumptions not backed by conversation evidence

7. Each topic file should contain:
   - Everything known about the subject — stable reference facts and the user's preferences, together in clear prose
   - Relevant context or details, kept concise
   - Keep files under ~50 lines. If a subject needs more detail, the detail probably belongs in a project file or episodic memory, not in a topic file.

8. **Important constraints**:
   - Only create or modify files within \`$WORKSPACE/memories/topics/\`
   - Use descriptive, topic-based filenames (not dates). Good names: \`work-info.md\`, \`key-people.md\`, \`tech-stack.md\`, \`communication-style.md\`. Bad names that indicate the content belongs in episodic: \`2026-04-15-outage.md\`, \`bug-fix-session.md\`, \`security-incident-april.md\`. For per-incident fragmentation patterns to avoid, see step 4's negative examples.
   - If no new information emerged from the conversation, it is perfectly acceptable to create no files
   - Do not infer information that wasn't explicitly stated — only record what was actually shared or discussed
   - Before writing content, ask: "Will this still be useful in a month?" If no — it describes something that happened once, has a specific date, or is a record of an event — it belongs in episodic memory, not here
   - Do not restate content already captured in a context file (see the Context File Deduplication section below)

Remember: These memories help the assistant maintain context across sessions. Focus on durable, accurate knowledge about each subject — both the reference facts and the preferences — not activity logs or documents.`;

// Folded into the topics instruction so ONE extraction pass classifies every signal as topic content
// or learning content and writes it to exactly one store (R4/R6). Self-scoped to memories/learnings/
// for learning signals; the authoritative two-directory write surface is the combined scopeSection +
// SILENT_BACKGROUND_SECTION at the end of the instruction.
const LEARNINGS_EXTRACTION_SECTION = `## Learnings — what bites and what worked

This same pass also captures **experience**, distinct in kind from the topics knowledge above. A learning is recurring friction, a hard constraint, a repeated failure, or a reflection about an approach that worked or turned out to be a dead end — something that *bites* or would bite again, not something that is merely true. Experience is what is missing if every session rediscovers the same gotchas.

### What belongs in learnings

- **Recurring friction**: an uncooperative test suite, a deploy step that keeps failing, a tool whose default behavior keeps surprising you.
- **Hard constraints the user enforces**: rules, gates, or limits that must be respected across sessions.
- **Repeated failures**: the same mistake or misstep made more than once.
- **Reflections**: an approach that worked well, or one that turned out to be a dead end.

### What does NOT belong in learnings

- **One-time events** — a single bug fix, a one-off outage, an incident that happened once on a specific date. Those are episodic narrative, not experience; they only become a learning if the SAME friction recurs. Leave them out.
- **A restatement of a topic or a context file.** If something is already stable knowledge (a fact or a preference), it is a topic — do not also record it as a learning. A learning is the *friction* a fact causes, not the fact itself.

### Classify each signal once — topics or learnings, never both

For every signal you consider, decide its PRIMARY aspect in context and write it to exactly ONE store:
- Stable what/why knowledge and the user's preferences → \`memories/topics/\` (the instructions above).
- Recurring friction, a hard constraint, a repeated failure, or a reflection → \`memories/learnings/\`.

When a signal has BOTH a stable-knowledge and a friction character (a fact that causes friction, a constraint that is also a preference), record it by its **primary** aspect in one store only — the two stores never duplicate. A fact-that-bites is recorded in \`memories/learnings/\` as the lesson (what bites), not also restated as a topic.

### Read existing learnings before deciding new vs recurring

Before writing, **read the existing files in \`$WORKSPACE/memories/learnings/\`** to see what friction is already recorded. Then for each learning signal:

- **New friction** — no existing draft or confirmed entry matches (a first sighting) → append it under \`## Drafts\` in the matching slug file. A first sighting is tentative: it does not yet establish a recurring pattern.
- **Matches an existing draft** — this session's friction is the same subject and the same kind of problem as a \`## Drafts\` entry → promote it by moving that draft from \`## Drafts\` to \`## Confirmed\`. Repetition is the signal this is a real, recurring learning.
- **The friction was resolved** in this conversation — the flaky test now passes, the deploy step no longer fails, the user confirms a constraint was lifted → correct or remove the existing entry instead. A resolved friction is the opposite of recurrence; never promote it.

### File model and slugs

Each learnings file is one theme of recurring friction, broad and future-mergeable:
- Two sections per \`<slug>.md\`: \`## Drafts\` (tentative) and \`## Confirmed\` (corroborated across sessions). A new file starts with its first sighting under \`## Drafts\`; \`## Confirmed\` fills in as drafts are promoted.
- Follow the same broad-slug conventions as topics (see "File Consolidation at Write Time" above): one theme per file, named for the friction (\`test-suite.md\`, \`deploys.md\`, \`<tool>.md\`), never incident- or date-scoped. Reuse the broadest existing file that covers the theme; only create a new one when no existing file fits.
- Keep files concise (under ~50 lines). If a file grows past one theme, split it into its own broad slug.

Learnings write only within \`$WORKSPACE/memories/learnings/\`.`;

// Extraction runs as a silent follow-up turn on a fork of the just-ended conversation. The
// forked agent still has its full tool set and persona, so it must be told this is background
// file maintenance — no chat reply, no messaging/notification/task tools. Accepts one store or
// several: the topics+learnings fork writes two directories, so its silent clause must name both.
const SILENT_BACKGROUND_SECTION = (stores: MemoryStore | readonly MemoryStore[]): string => {
  const list = Array.isArray(stores) ? [...stores] : [stores];
  const dirs = list.map((store) => `\`$WORKSPACE/memories/${store}/\``).join(" and ");

  return `## Background Maintenance Step

This is a SILENT background memory-maintenance step, not part of the conversation. Do NOT send any
user-facing message, ask any question, or produce a chat reply, and do NOT use any messaging,
notification, or task-management tools. Only read files in the workspace and create or modify files
under ${dirs}. When you are done, simply stop — your only output is the
file changes.`;
};

// Keyed on ExtractionStore, not MemoryStore: only stores that get their own extraction fork have an
// instruction. Learnings is folded into the topics fork, so it has no entry here — and the type now
// enforces that (R4) at compile time rather than leaving STORE_INSTRUCTIONS["learnings"] undefined.
const STORE_INSTRUCTIONS: Record<ExtractionStore, string> = {
  episodic: [
    EPISODIC_BASE_PROMPT,
    scopeSection("episodic"),
    SILENT_BACKGROUND_SECTION("episodic"),
  ].join("\n\n"),
  topics: [
    TOPICS_BASE_PROMPT,
    LEARNINGS_EXTRACTION_SECTION, // folded in — this fork classifies each signal as topic or learning (R4/R6)
    STORE_PURPOSE_SECTION, // carries the learnings authority bullet (Batch 1)
    CONTEXT_DEDUP_SECTION,
    WORKSPACE_VALIDATION_SECTION,
    INDEX_UPDATE_SECTION, // the fork writes both stores, so it keeps both MEMORY.md indexes in sync
    scopeSection(["topics", "learnings"]), // both directories — authoritative two-dir write surface
    SILENT_BACKGROUND_SECTION(["topics", "learnings"]),
  ].join("\n\n"),
};

/**
 * The follow-up user instruction handed to the forked conversation for one memory store. `date`
 * (`YYYY-MM-DD`) is the day the conversation belongs to — it dates the episodic file. It MUST be the
 * trunk's own day, not wall-clock: a trunk closed late (nightly miss, recovery, multi-day downtime)
 * still files its memories under the day it happened. Defaults to today for non-close callers.
 */
export const storeInstruction = (
  store: ExtractionStore,
  workspaceRoot: string,
  date: string = localIsoDate(),
): string =>
  STORE_INSTRUCTIONS[store].replaceAll("$WORKSPACE", workspaceRoot).replaceAll("{date}", date);
