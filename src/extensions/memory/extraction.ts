import type { SideRunner } from "../../agent/side-run.ts";
import { localIsoDate } from "../../util/dates.ts";
import type { MemoryStore } from "./layout.ts";
import {
  CLASSIFICATION_EXAMPLES_SECTION,
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

const FACTS_BASE_PROMPT = `We just finished the conversation above. Using what you already know from it, extract or update factual information that would be useful to remember for future conversations.

## Instructions

1. **Read existing files** in \`$WORKSPACE/memories/facts/\` to see what facts are already stored.

2. Analyze the conversation for STABLE REFERENCE INFORMATION — things that stay true across conversations:
   - Personal details about the user (job, location, family, contacts)
   - Important dates, deadlines, or upcoming events
   - Stable routines or commitments (structure only, not daily logs)
   - Key people and their roles/relationships
   - Technical decisions or configurations that affect future work
   - Account info, service subscriptions, tool setups

   DO NOT store as facts:
   - Daily activity logs or status updates (that's episodic memory)
   - One-time events: bug fixes, security incidents, feature completions, outages, deployment events — these happened once on a specific date and belong in episodic memory, not here
   - Full design documents, specs, or game mechanics (project files)
   - Anything longer than ~40 lines — if it needs that much space, it's not a fact, it's a document

3. **Before creating a new file**, search for existing overlap:
   - Use the grep tool to search existing files for the key topic or keywords
   - If an existing file covers the same topic, UPDATE that file instead of creating a new one
   - If information is spread across multiple files about the same topic, MERGE them into one file and delete the others

4. **File Consolidation at Write Time**:
   Before creating any new file, follow this mandatory sequence:
   - First, list the target directory \`$WORKSPACE/memories/facts/\` with the ls tool.
   - Identify which existing file (if any) covers the broadest topic that encompasses the new information. Match by project name, system name, tool, or domain — not by incident, date, or specific event.
   - If a broad-topic file exists, UPDATE that file. Do NOT create an incident- or date-specific sibling alongside it (e.g., if \`<project>.md\` exists, do not also create \`<project>-<bug-description>-<YYYY-MM-DD>.md\`).
   - If multiple existing files cover overlapping aspects of the same topic, prefer the most specific existing file that still covers the new information broadly — and consider merging the narrower ones into it.
   - Only create a new file when NO existing file covers the topic. When you do create one, choose a broad topic name that future related extracts can merge into — \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, \`<domain>.md\` — never a name scoped to one incident, bug, patch, or date.
   - Positive examples (broad, future-mergeable): \`<project>.md\`, \`<system>.md\`, \`<tool>.md\`, \`<domain>.md\`, \`work-info.md\`, \`tech-stack.md\`.
   - Negative examples (forbidden — too narrow): \`<project>-<bug-description>-<YYYY-MM-DD>.md\`, \`<project>-patch-<issue-id>.md\`, \`<system>-<incident>-<date>.md\`, \`<topic>-session-<date>.md\`.

5. Manage the fact files:
   - Create new files with descriptive names ONLY when no existing file covers the topic
   - Update existing files when new information extends what's there
   - **Merge** files that overlap in topic — combine into one, delete the rest
   - **Delete** files that are outdated, redundant, or better covered elsewhere
   - When updating a file, READ it first. If a section already covers what you're about to add, update that section rather than appending a duplicate

6. **Prune stale and redundant entries**:
   - After reading existing files, actively look for entries that may be outdated or no longer accurate based on the conversation:
     - Information contradicted by new statements (e.g., file says "works at Company A" but conversation reveals a move to Company B)
     - References to completed projects, past roles, or expired commitments that the conversation confirms are done
     - Entries about tools, services, or setups the user no longer uses
   - When you find stale entries: update them if new information replaces the old, or delete the file if the entire topic is no longer relevant
   - **Do NOT prune based on**: vague hints ("I might switch..."), old dates alone (age is not staleness), or assumptions not backed by conversation evidence

7. Each fact file should contain:
   - Clear, factual statements
   - Relevant context or details
   - Keep files under 40 lines. If a topic needs more detail, the detail probably belongs in a project file, not in facts memory.

8. **Important constraints**:
   - Only create or modify files within \`$WORKSPACE/memories/facts/\`
   - Use descriptive, topic-based filenames (not dates). Good names: \`work-info.md\`, \`key-people.md\`, \`tech-stack.md\`. Bad names that indicate the content belongs in episodic: \`2026-04-15-outage.md\`, \`bug-fix-session.md\`, \`security-incident-april.md\`. For per-incident fragmentation patterns to avoid, see step 4's negative examples.
   - If no new factual information emerged from the conversation, it is perfectly acceptable to create no files
   - Do not infer facts that weren't explicitly stated — only record what was actually shared or discussed
   - Before writing a fact, ask: "Will this still be useful in a month?" If no — it describes something that happened once, has a specific date, or is a record of an event — it belongs in episodic memory, not here

Remember: These memories help the assistant maintain context across sessions. Focus on accurate, stable reference information — not activity logs or documents.`;

const PREFERENCES_BASE_PROMPT = `We just finished the conversation above. Using what you already know from it, extract or update the user's expressed preferences.

## Instructions

1. **Read existing files** in \`$WORKSPACE/memories/preferences/\` to see what preferences are already stored. Also read \`$WORKSPACE/AGENTS.md\` if it exists — this file contains operational instructions and workflow preferences. If it doesn't exist or is empty, proceed normally — this check is purely to avoid duplication.

2. Analyze the conversation for SUBJECTIVE CHOICES about how things should be done:
   - How they like things done (communication style, workflows, formats)
   - Approaches they prefer or want to avoid
   - Tool, framework, or methodology preferences
   - Scheduling or organizational preferences

   A preference is NOT:
   - A factual detail (job title, project architecture) → facts memory
   - A design decision or spec (game mechanics, system rules) → project files
   - A behavioral instruction for the assistant → AGENTS.md context file
   - A detailed description of a system or project → too detailed for prefs
   - An implementation detail or system behavior (how something works technically) → facts memory or project docs
   - A bug report, resolved issue, or one-time fix → transient, not a lasting preference
   - **Financial reference data** (payment structures, rates, fee schedules, reconciliation procedures) → facts memory
   - **Technical specifications** (environment variables, SDK/API limitations, tool capabilities, system requirements) → facts memory
   - **Procedural workflows** (cleanup steps, diagnostic procedures, deployment sequences) → facts memory
   - **System configuration records** (what was configured, where, with what values) → facts memory

2b. **Classification self-check**: Before writing any file, ask yourself: "Is this describing HOW SOMETHING WORKS (a fact) or HOW THE USER WANTS IT DONE (a preference)?"
   - "How it works" → route to facts memory — do NOT create a preferences file
   - "How the user wants it done" → valid preference, proceed
   - When uncertain, prefer facts over preferences — objective information does not become a preference just because the user mentioned it

3. **Before creating a new file**, search for existing overlap:
   - Use the grep tool to search existing files for the key topic or keywords
   - Also search \`$WORKSPACE/AGENTS.md\` for the same topic. If AGENTS.md already captures the preference (even in different words), skip creating a new file — the information is already stored where it belongs
   - If an existing file covers the same topic, UPDATE that file instead of creating a new one
   - If the same preference appears in multiple files, consolidate into the most specific file and remove it from the others

4. **File Consolidation at Write Time**:
   Before creating any new file, follow this mandatory sequence:
   - First, list the target directory \`$WORKSPACE/memories/preferences/\` with the ls tool.
   - Identify which existing file (if any) covers the broadest preference topic that encompasses the new information. Match by topic area (style, workflow, communication, tooling), project, system, or domain — not by a specific occasion, date, or one-off interaction.
   - If a broad-topic file exists, UPDATE that file. Do NOT create an occasion- or date-specific sibling alongside it (e.g., if \`<topic-area>-style.md\` exists, do not also create \`<topic-area>-feedback-<YYYY-MM-DD>.md\`).
   - If multiple existing files cover overlapping aspects of the same topic, prefer the most specific existing file that still covers the new information broadly — and consider merging the narrower ones into it.
   - Only create a new file when NO existing file covers the topic. When you do create one, choose a broad topic name that future related extracts can merge into — \`<topic-area>-style.md\`, \`<topic-area>-workflow.md\`, \`<domain>.md\`, \`<project>.md\` — never a name scoped to one occasion, feedback moment, or date.
   - Positive examples (broad, future-mergeable): \`<topic-area>-style.md\`, \`<topic-area>-workflow.md\`, \`<domain>.md\`, \`<project>.md\`, \`communication-style.md\`, \`code-formatting.md\`.
   - Negative examples (forbidden — too narrow): \`<topic-area>-feedback-<YYYY-MM-DD>.md\`, \`<project>-preference-<issue-id>.md\`, \`<topic-area>-session-<date>.md\`, \`<one-off>-incident-<date>.md\`.

5. Manage the preference files:
   - Create new files with descriptive names ONLY when no existing file covers the topic
   - When updating a file, READ it first. If it already says what you're about to add, do not add it again. If it says something similar in different words, REPLACE the old version — don't add a second version.
   - Delete or merge files that overlap
   - Each file should have ONE clear statement per preference, not multiple sections restating the same thing in different words

6. **Prune stale and reversed preferences**:
   - After reading existing files, actively look for preferences that may no longer reflect the user's current stance:
     - Reversed preferences (e.g., file says "prefers dark mode" but user now says "I switched to light mode")
     - Preferences about tools or workflows the user has explicitly moved away from
     - Preferences that the conversation contradicts with clear, stated alternatives
   - When you find stale preferences: update the file if the user expressed a new preference on the same topic, or delete the file if the preference topic is no longer relevant
   - **Do NOT prune based on**: vague hints ("I might try..."), single exceptions to general rules, or assumptions not backed by conversation evidence

7. Each preference file should contain:
   - A clear statement of the preference
   - Brief context or an example (1-2 sentences)
   - When appropriate, how strongly the preference is held
   - Keep files under 30 lines. A preference that takes more to express is probably a spec or design document, not a preference.

8. **Important constraints**:
   - Only create or modify files within \`$WORKSPACE/memories/preferences/\`
   - Use descriptive, topic-based filenames (not dates). Good names: \`communication-style.md\`, \`code-formatting.md\`, \`<topic-area>-workflow.md\`. For per-occasion fragmentation patterns to avoid, see step 4's negative examples.
   - If no preference-related information emerged from the conversation, it is perfectly acceptable to create no files
   - Do not infer preferences from silence — only record what the user actually expressed

Remember: These memories help the assistant tailor its approach to the user's preferences. Focus on genuine, stated choices — not facts, specs, or instructions.`;

// Extraction runs as a silent follow-up turn on a fork of the just-ended conversation. The
// forked agent still has its full tool set and persona, so it must be told this is background
// file maintenance — no chat reply, no messaging/notification/task tools.
const SILENT_BACKGROUND_SECTION = (store: MemoryStore): string => `## Background Maintenance Step

This is a SILENT background memory-maintenance step, not part of the conversation. Do NOT send any
user-facing message, ask any question, or produce a chat reply, and do NOT use any messaging,
notification, or task-management tools. Only read files in the workspace and create or modify files
under \`$WORKSPACE/memories/${store}/\`. When you are done, simply stop — your only output is the
file changes.`;

const STORE_INSTRUCTIONS: Record<MemoryStore, string> = {
  episodic: [
    EPISODIC_BASE_PROMPT,
    scopeSection("episodic"),
    SILENT_BACKGROUND_SECTION("episodic"),
  ].join("\n\n"),
  facts: [
    FACTS_BASE_PROMPT,
    CLASSIFICATION_EXAMPLES_SECTION,
    STORE_PURPOSE_SECTION,
    CONTEXT_DEDUP_SECTION,
    WORKSPACE_VALIDATION_SECTION,
    INDEX_UPDATE_SECTION,
    scopeSection("facts"),
    SILENT_BACKGROUND_SECTION("facts"),
  ].join("\n\n"),
  preferences: [
    PREFERENCES_BASE_PROMPT,
    CLASSIFICATION_EXAMPLES_SECTION,
    STORE_PURPOSE_SECTION,
    CONTEXT_DEDUP_SECTION,
    WORKSPACE_VALIDATION_SECTION,
    INDEX_UPDATE_SECTION,
    scopeSection("preferences"),
    SILENT_BACKGROUND_SECTION("preferences"),
  ].join("\n\n"),
};

/** The follow-up user instruction handed to the forked conversation for one memory store. */
export const storeInstruction = (store: MemoryStore, workspaceRoot: string): string =>
  STORE_INSTRUCTIONS[store]
    .replaceAll("$WORKSPACE", workspaceRoot)
    .replaceAll("{date}", localIsoDate());
