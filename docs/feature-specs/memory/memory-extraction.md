# Memory Extraction

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

After a conversation ends, the system automatically extracts and persists learnings as structured markdown files. Each memory type has its own processor that forks the original SDK session and directs the agent to read existing memories, analyze the conversation, and create, update, or delete memory files as needed. Three memory types: episodic (date-stamped conversation summaries), facts (named files about the user and other factual information), and preferences (named files about how the user likes things). All memories are human-readable markdown in the workspace. A nightly maintenance system reviews stored memories, consolidating episodic entries, pruning stale facts, and deduplicating preferences.

The memory workspace also includes `memories/transcripts/`, a non-extractive subdirectory populated by a transcript archive processor that copies each conversation's SDK transcript into the workspace on session close. Archived transcripts are raw `.jsonl` files (not curated markdown) and are named by SDK session ID so the archived path is derivable without a dedicated model field.

## User Stories

- As the system, I need to automatically extract and persist learnings from completed conversations so that future sessions are contextually aware of past interactions, known preferences, and prior decisions
- As a user, I want my memories stored as readable markdown files so that I can inspect, understand, and edit them directly
- As the system, I need to periodically review and clean up stored memories so that the memory store remains useful and doesn't degrade over time from accumulated staleness, redundancy, and excessive verbosity

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Automatically extract and persist learnings from completed conversations as structured markdown files |
| R1 | Each memory type has its own processor that forks the original SDK session to analyze the conversation |
| R2 | Forked agents autonomously manage memory files — processor code performs no file I/O |
| R3 | Three memory types with distinct extraction strategies: episodic (date-stamped summaries), facts (topic-named factual information), preferences (topic-named user preferences) |
| R4 | Memories organized in subdirectories: `memories/episodic/`, `memories/facts/`, `memories/preferences/`, `memories/transcripts/` |
| R5 | Memory files are human-readable markdown, directly inspectable and editable |
| R6 | Bootstrap hook creates the memory directory structure on first run (idempotent) |
| R7 | Transcript archive processor copies each session's SDK transcript into `memories/transcripts/<sdk-session-id>.jsonl` on session close; runs in the post-processing `pre_finalize` phase so archived files land before the workspace git commit |
| R8 | Transcript archival is best-effort — failures (missing source, filesystem errors) are logged and swallowed, never crashing the conversation or blocking other post-processing |
| R9 | Before writing facts or preferences memories that reference workspace state (file paths, configuration values, implementation details), forked agents validate claims against actual workspace files using internal sub-agents; invalid claims are omitted |
| R10 | Facts and preferences processors proactively prune stale, outdated, or superseded entries during extraction — removing or updating entries that the conversation contradicts, and merging overlapping files into one; episodic entries are never deleted for content reasons (only malformed filenames are consolidated) |
| R11 | Before creating facts or preferences memory files, forked agents read the foundational context files from `$WORKSPACE/context/` (AGENTS.md, USER.md, SOUL.md) and skip creation when the information is already covered there; context files are the authoritative source for their respective categories; additionally, when the context summary lists active skills with directory paths, forked agents read their SKILL.md before creating files — skill files are the authoritative source for their domain; both processors use a shared `CONTEXT_DEDUP_SECTION` prompt constant that combines context file checking and skill dedup into a single unified section |
| R12 | The preferences processor also checks `AGENTS.md` inline before creating new preference files — if the information is already captured there (even in different words), the processor skips creating the file; gracefully proceeds normally if `AGENTS.md` doesn't exist or is empty |
| R13 | Four nightly maintenance jobs — one per memory type (episodic, facts, preferences) and one for context files — share a single configurable cron schedule and can be disabled via an `enabled` toggle |
| R14 | Episodic maintenance applies tiered time windows: clean daily notes for verbosity (last N days), consolidate into weekly summaries, consolidate into monthly summaries, delete entries older than the configured monthly threshold |
| R15 | Facts maintenance evaluates files for staleness, redundancy, and overlap — consolidating, editing within files, merging files, or removing obsolete files |
| R16 | Preferences maintenance evaluates files for redundancy and overlap across files — consolidating, editing within files, merging files, or removing obsolete files |
| R17 | All maintenance is agent-driven — agents read and edit memory files using fresh SDK sessions with read access anywhere in the workspace, write/edit access scoped to the target memory subdirectory, and bash restricted to utility commands plus `rm` for file deletion |
| R18 | Maintenance is idempotent — running it multiple times produces the same result |
| R19 | Maintenance handles errors gracefully — agent failures, malformed files, empty stores, and concurrent access do not corrupt the memory store; changes are automatically committed to git after each job completes |
| R20 | Tiered time window thresholds and the maintenance schedule are configurable with sensible defaults |
| R21 | A fourth maintenance job cleans up foundational context files (SOUL.md, USER.md, AGENTS.md) — evaluating for staleness, redundancy, and overlap, enforcing size limits, and removing stale content — without adding new content; runs on the same schedule and governed by the same `enabled` toggle as the memory maintenance jobs |
| R22 | Maintenance ticks (facts, preferences, context) receive the full skill catalog (name, description, absolute directory path) when a SkillRegistry is available, enabling agents to identify and remove context/memory entries that duplicate skill content; graceful no-op when the registry is unavailable or empty |
| R23 | Both facts and preferences processors include a shared `STORE_PURPOSE_SECTION` defining the authority hierarchy (Skills > Memory facts > Context files) to guide correct information routing during extraction |
| R24 | Maintenance ticks (facts, preferences, context) receive a cross-store file manifest listing files in other information stores (episodic, facts, preferences, context) so agents can detect cross-store contradictions; a shared `_build_cross_store_manifest` helper builds manifests from filesystem listings; the manifest lists file names and paths only, not content |
| R25 | Maintenance ticks (facts, preferences, context) include contradiction detection instructions referencing the authority hierarchy (Skills > Memory facts > Context files); when a contradiction is found, the less authoritative store is updated or trimmed; a shared `CONTRADICTION_DETECTION_SECTION` prompt constant provides uniform instructions |
| R26 | Maintenance ticks (facts, preferences, context) include `STORE_PURPOSE_SECTION` defining each store's role and the authority hierarchy, ensuring maintenance agents understand which store should hold what information |
| R27 | `build_context_summary` includes the authority hierarchy (Skills > Memory facts > Context files) in its closing instructions, informing extraction processors about information routing priorities |
| R28 | Facts and preferences extraction prompts include a "File Consolidation at Write Time" section instructing agents to list the target directory before creating any new file, identify the broadest existing file that covers the topic, prefer updating that file over creating an incident- or date-specific sibling, and — when creation is unavoidable — use a broad topic name (project, system, tool, domain, or topic-area) so future related extracts can merge in |
| R29 | Facts and preferences maintenance prompts include a "Cluster Consolidation" subsection instructing agents to group files by shared prefix or core topic and, when 3 or more files share a prefix/topic, merge them into a single broad-topic file using the same naming convention as extraction; episodic and context maintenance are excluded — episodic naming is date-based by design and context maintenance operates on a fixed three-file set |

## Behaviors

### Session Forking and Memory Extraction (R1, R2)

Each memory processor forks the original SDK session and sends a tailored extraction prompt. The forked agent reads the relevant memory subdirectory, analyzes the conversation, and autonomously creates, updates, or deletes memory files.

**Acceptance Criteria**:
- Given a closed session with a valid SDK session ID, when a memory processor runs, then it forks the session via the standalone `query()` function with `resume` and `fork_session=True`
- Given a forked agent session, when the extraction prompt is sent, then the agent has full workspace access and operates from the workspace directory
- Given a forked agent for a specific memory type, when it executes, then the agent autonomously reads the corresponding subdirectory and manages files — the processor code performs no file I/O
- Given a forked session, when the agent completes its extraction, then the async iterator is fully consumed and the forked session ends cleanly

### Episodic Memories (R3)

Date-stamped summaries of conversations, consolidated over time.

**Acceptance Criteria**:
- Given a completed conversation with meaningful content, when the episodic processor runs, then the forked agent creates or updates exactly one date-stamped file (`YYYY-MM-DD.md`) in `memories/episodic/` — no variant filenames (e.g., `-consolidated`, `-final`) are permitted
- Given multiple conversations on the same day, when the episodic processor runs, then the agent reads the existing day file and edits it to merge new content rather than creating a second file
- Given a trivial conversation, when the episodic processor runs, then the agent may determine there's nothing meaningful to record — no file creation is forced
- Given an episodic entry with valid content, when the episodic processor runs, it is never deleted based on content — only malformed filenames are consolidated into the canonical daily file (R10)

### Facts Memories (R3)

Named files about the user and other stable reference information — personal details, key people, technical decisions, routines — updated when new information emerges. One-time events (bug fixes, security incidents, feature completions, outages, deployments) are not facts — they belong in episodic memory.

**Acceptance Criteria**:
- Given a conversation where new factual information is revealed, when the facts processor runs, then the forked agent searches existing files for topic overlap and updates the matching file, or creates a new topic-named file in `memories/facts/` only when no existing file covers the topic
- Given previously stored factual information is contradicted, when the facts processor runs, then the agent updates the existing file with corrected information
- Given a previously stored fact becomes invalid, when the facts processor runs, then the agent may delete the obsolete file
- Given information about a topic is spread across multiple files, when the facts processor runs, then the agent merges them into a single file and deletes the redundant ones
- Given a facts file contains entries contradicted by the conversation, when the facts processor runs, then the agent updates or removes the stale entries (R10)
- Given ambiguous signals ("I might switch..."), when the facts processor runs, then it retains existing entries unchanged — only clear evidence triggers pruning (R10)
- Given a conversation about a one-time event (bug fix, security incident, outage, deployment), when the facts processor runs, then it does not create a facts file — that content belongs in episodic memory
- Given a workspace where a broad-topic file (`<project>.md`, `<system>.md`, etc.) already exists in `memories/facts/`, when a conversation produces new facts about that same project/system, then the agent updates the existing broad-topic file and does not create an incident- or date-specific sibling (R28)
- Given a workspace with no facts file covering the conversation's topic, when the facts processor creates a new file, then the filename is a broad topic name (project, system, tool, or domain) that future related extracts can merge into — never an incident-, bug-, patch-, or date-specific name (R28)

### Preferences Memories (R3)

Named files about how the user likes things — code style, communication, workflow — updated or deleted when preferences change.

**Acceptance Criteria**:
- Given a conversation where the user expresses a preference, when the preferences processor runs, then the forked agent searches existing files for topic overlap and updates the matching file, or creates a new topic-named file in `memories/preferences/` only when no existing file covers the topic
- Given a user changes a previously expressed preference, when the preferences processor runs, then the agent updates or deletes the existing file
- Given a conversation with no preference-related content, when the preferences processor runs, then no changes are made
- Given the same preference appears in multiple files, when the preferences processor runs, then the agent consolidates into the most specific file and removes duplicates
- Given a preferences file contains entries the user has reversed or moved away from, when the preferences processor runs, then the agent updates or removes the stale entries (R10)
- Given ambiguous signals ("I might try..."), when the preferences processor runs, then it retains existing entries unchanged — only clear evidence triggers pruning (R10)
- Given a preference already captured in `AGENTS.md`, when the preferences processor runs, then no preference file is created — the information is already stored where it belongs (R12)
- Given `AGENTS.md` doesn't exist or is empty, when the preferences processor runs, then preference extraction proceeds normally without the dedup check (R12)
- Given a workspace where a broad-topic preference file (`<topic-area>-style.md`, `<topic-area>-workflow.md`, etc.) already exists in `memories/preferences/`, when a conversation produces new preferences in that same topic area, then the agent updates the existing broad-topic file and does not create an occasion- or date-specific sibling (R28)
- Given a workspace with no preference file covering the conversation's topic, when the preferences processor creates a new file, then the filename is a broad topic name (topic-area-style, topic-area-workflow, domain, or project) that future related extracts can merge into — never an occasion-, feedback-, or date-specific name (R28)

### Directory Structure and Bootstrap (R4, R6)

**Acceptance Criteria**:
- Given no `memories/` directory exists, when the memory bootstrap hook runs, then `memories/`, `memories/episodic/`, `memories/facts/`, `memories/preferences/`, and `memories/transcripts/` are created
- Given the directory structure already exists, when the hook runs, then nothing changes (idempotent)

### Transcript Archival (R7, R8)

On session close, the transcript archive processor copies the SDK-owned transcript into the workspace so it survives SDK storage changes and is committed to git alongside other workspace memory.

**Acceptance Criteria**:
- Given a closed session with populated `transcript_path` and `sdk_session_id`, when the archive processor runs, then the transcript is copied to `memories/transcripts/<sdk-session-id>.jsonl`
- Given the archive processor runs in the `pre_finalize` phase, when the pipeline advances to `finalize`, then the archived file is visible to the git commit processor and included in the auto-commit
- Given `transcript_path` or `sdk_session_id` is null, when the archive processor runs, then it skips with a debug log and completes normally
- Given the SDK transcript no longer exists at `transcript_path`, when the archive processor runs, then it logs a warning and completes normally
- Given a filesystem error occurs during the copy (permissions, disk full), when the archive processor runs, then it logs the error and completes normally — other processors and phases are unaffected
- Given `memories/transcripts/` was removed after bootstrap, when the archive processor runs, then it recreates the directory on demand before copying
- Given the processor runs more than once for the same session, when the copy executes, then the destination is overwritten (idempotent)

### Human Readability (R5)

**Acceptance Criteria**:
- Given memory files exist, when the user navigates to `memories/`, then they can read and understand the contents in any text editor
- Given a user manually edits a memory file, when the next extraction runs, then the agent sees and respects the edits

### Workspace Claim Validation (R9)

Before writing facts or preferences memory files that contain claims about workspace state, the forked agent validates those claims against actual files using lightweight internal sub-agents. Episodic memories are conversation summaries and do not reference workspace state, so validation does not apply to the episodic processor.

**Acceptance Criteria**:
- Given a memory referencing a non-existent file path, when the agent prepares to write, the claim is omitted from the memory file
- Given a memory with accurate workspace claims, when the agent prepares to write, all claims are included
- Given a memory with no workspace references (e.g., personal details, preferences, conversation summaries), when the agent prepares to write, no validation overhead is added
- Given the agent identifies a verifiable claim, it spawns a read-only sub-agent to check the claim against the actual file and omits the claim if invalid

### Context File Deduplication (R11, R12, R23)

Before creating facts or preferences memory files, the forked agent reads the foundational context files and skips creation when the information is already covered there. Both processors use the shared `CONTEXT_DEDUP_SECTION` prompt that combines context file checking (AGENTS.md, USER.md, SOUL.md) and skill dedup (reading active skill files listed in the context summary) into a single unified section. Preferences has an additional inline AGENTS.md check integrated directly into its extraction steps, providing a focused dedup that specifically targets operational and workflow preferences. Both processors also include the shared `STORE_PURPOSE_SECTION` defining the authority hierarchy (Skills > Memory facts > Context files).

**Acceptance Criteria**:
- Given a fact topic already covered in AGENTS.md, USER.md, or SOUL.md, when the facts processor runs, then the agent does not create a separate memory file
- Given a fact topic already covered by an active skill file (listed in the context summary with a directory path), when the facts processor runs, then the agent reads the skill's SKILL.md and does not create a memory file that duplicates the skill's content
- Given a preference topic already covered in AGENTS.md, when the preferences processor runs, then the agent does not create a separate memory file (R12)
- Given a preference topic already covered by an active skill file (listed in the context summary with a directory path), when the preferences processor runs, then the agent reads the skill's SKILL.md and does not create a memory file that duplicates the skill's content
- Given a context file partially covers the topic but the conversation adds genuinely new details, when the agent runs, then it creates a file for the new information only
- Given no context file or skill covers the topic, when the agent runs, then it proceeds normally with file creation
- Given `AGENTS.md` doesn't exist or is empty, when the preferences processor runs, then extraction proceeds normally without the inline dedup check (R12)
- Given the context summary lists no active skills, when either processor runs, then skill dedup guidance is harmlessly inert — no additional reads are performed
- Given a conversation where new information emerges, when either the facts or preferences processor runs, then the `STORE_PURPOSE_SECTION` is included in the prompt, informing the agent of the authority hierarchy for correct information routing (R23)

### Scheduled Maintenance (R13, R17)

Four maintenance jobs run on a shared nightly cron schedule. Each creates a fresh SDK session with scoped writer permissions — read access anywhere in the workspace, edit/write scoped to the target directory, bash restricted to utility commands (plus `rm` for memory jobs). The agent reads the target files, performs maintenance autonomously, and commits changes to git.

**Acceptance Criteria**:
- Given maintenance is enabled in configuration, when the system starts, then four maintenance jobs are scheduled for periodic execution (episodic, facts, preferences, context)
- Given a maintenance job fires, when the agent runs, then it uses a fresh SDK session with scoped access to the relevant directory
- Given a maintenance agent completes its work, then all file changes are committed to git — staging only the affected directory
- Given the memory store is empty or the context directory is absent, when a maintenance task runs, then it completes as a no-op with no git commit
- Given maintenance is disabled in configuration, when the system starts, then no maintenance jobs are registered

### Episodic Maintenance (R14)

Applies tiered time-window consolidation: recent daily files are cleaned for verbosity, older files are consolidated into weekly summaries, even older files into monthly summaries, and files past the retention threshold are deleted. Tiered thresholds are configurable with sensible defaults.

**Acceptance Criteria**:
- Given episodic files within the recent window, when the maintenance agent processes them, then it reduces verbosity without deleting files or removing substantive content
- Given episodic files in the weekly consolidation window, when the agent processes them, then it consolidates daily files into weekly summary files (YYYY-WNN.md), removing originals
- Given episodic files in the monthly consolidation window, when the agent processes them, then it consolidates into monthly summary files (YYYY-MM.md), removing originals
- Given episodic files older than the configured monthly threshold, when the agent processes them, then they are deleted
- Given a weekly or monthly consolidation would create a summary file that already exists, when the agent processes the relevant files, then it merges new content into the existing summary
- Given partial groups at week/month boundaries, when the agent processes them, then they are consolidated into a single summary for that period

### Facts Maintenance (R15, R22, R24, R25, R26)

Evaluates fact files for staleness (outdated information), redundancy (duplicate information across files), and overlap (related topics split across files). The agent consolidates, edits, merges, or removes files as needed. When the skill registry is available, the agent also receives the full skill catalog and can remove entries that duplicate skill content. The tick also receives a cross-store manifest listing files in other stores (preferences, context, episodic), contradiction detection instructions referencing the authority hierarchy, and store-purpose definitions.

**Acceptance Criteria**:
- Given the facts maintenance task runs, when it reads all fact files, then it evaluates each file for staleness, redundancy, and overlap
- Given stale entries (outdated dates, completed projects, contradicted information), when the agent identifies them, then it removes or updates the entries
- Given redundant entries (same information in different files), when the agent identifies duplicates, then it keeps the most complete version and removes duplicates
- Given overlapping files (related topics split across files), when the agent identifies overlap, then it merges into a single consolidated file and removes originals
- Given a fact file that is entirely obsolete, when the agent processes it, then it deletes the file
- Given the facts maintenance task runs with a non-empty skill registry, when the prompt is assembled, then it includes a skill catalog listing all skills with name, description, and absolute path, plus instructions to remove entries duplicating skill content
- Given the skill registry is unavailable or empty, when the facts maintenance task runs, then the prompt contains no skill catalog section — identical to behavior before R22
- Given the facts maintenance task runs, when the prompt is assembled, then it includes the `STORE_PURPOSE_SECTION` defining the authority hierarchy (R26)
- Given other information stores contain files, when the facts maintenance task runs, then the prompt includes a cross-store manifest listing those files by name and path (R24)
- Given no other stores contain files, when the facts maintenance task runs, then the manifest section is omitted
- Given the facts maintenance task runs, when the prompt is assembled, then it includes `CONTRADICTION_DETECTION_SECTION` instructing the agent to resolve contradictions in favor of the more authoritative store (R25)
- Given 3 or more files in `memories/facts/` share the same prefix or core topic (project, system, tool, or domain), when the facts maintenance task runs, then the agent merges the cluster into a single broad-topic file (`<project>.md`, `<system>.md`, etc.) and deletes the original narrow files (R29)
- Given fewer than 3 files share a prefix or topic, when the facts maintenance task runs, then it relies on the existing pairwise Overlap guidance rather than the Cluster Consolidation subsection — no forced consolidation occurs (R29)

### Preferences Maintenance (R16, R22, R24, R25, R26)

Evaluates preference files for redundancy (same preference stated multiple times) and overlap (related preferences split across files). The agent consolidates, edits, merges, or removes files as needed. When the skill registry is available, the agent also receives the full skill catalog and can remove entries that duplicate skill content. The tick also receives a cross-store manifest listing files in other stores (facts, context, episodic), contradiction detection instructions referencing the authority hierarchy, and store-purpose definitions.

**Acceptance Criteria**:
- Given the preferences maintenance task runs, when it reads all preference files, then it evaluates each file for redundancy and overlap
- Given redundant preferences, when the agent identifies duplicates, then it deduplicates — keeping the most complete version
- Given overlapping files, when the agent identifies overlap, then it merges into a single consolidated file and removes originals
- Given a preference file that is entirely superseded or obsolete, when the agent processes it, then it deletes the file
- Given the preferences maintenance task runs with a non-empty skill registry, when the prompt is assembled, then it includes a skill catalog listing all skills with name, description, and absolute path, plus instructions to remove entries duplicating skill content
- Given the skill registry is unavailable or empty, when the preferences maintenance task runs, then the prompt contains no skill catalog section — identical to behavior before R22
- Given the preferences maintenance task runs, when the prompt is assembled, then it includes the `STORE_PURPOSE_SECTION` defining the authority hierarchy (R26)
- Given other information stores contain files, when the preferences maintenance task runs, then the prompt includes a cross-store manifest listing those files by name and path (R24)
- Given no other stores contain files, when the preferences maintenance task runs, then the manifest section is omitted
- Given the preferences maintenance task runs, when the prompt is assembled, then it includes `CONTRADICTION_DETECTION_SECTION` instructing the agent to resolve contradictions in favor of the more authoritative store (R25)
- Given 3 or more files in `memories/preferences/` share the same prefix or core topic (style, workflow, communication, tooling, project, system, or domain), when the preferences maintenance task runs, then the agent merges the cluster into a single broad-topic file (`<topic-area>-style.md`, `<topic-area>-workflow.md`, etc.) and deletes the original narrow files (R29)
- Given fewer than 3 files share a prefix or topic, when the preferences maintenance task runs, then it relies on the existing pairwise Overlap guidance rather than the Cluster Consolidation subsection — no forced consolidation occurs (R29)

### Context Maintenance (R21, R22, R24, R25, R26)

Evaluates the three foundational context files (SOUL.md, USER.md, AGENTS.md) for staleness, redundancy, and overlap. The agent cleans up existing content — removing stale entries, consolidating duplicate sections, and enforcing size limits — without adding new content. This complements the `CoreContextProcessor`'s reactive cleanup by providing a periodic sweep that catches things that accumulate between conversations. When the skill registry is available, the agent also receives the full skill catalog and can remove entries that duplicate skill content. The tick also receives a cross-store manifest listing files in other stores (facts, preferences, episodic), contradiction detection instructions referencing the authority hierarchy, and store-purpose definitions.

**Acceptance Criteria**:
- Given the context maintenance task runs, when it reads all three context files, then it evaluates each for staleness, redundancy, and overlap
- Given stale entries (completed projects, resolved issues, outdated tool references), when the agent identifies them, then it removes or updates the entries
- Given redundant or overlapping sections within a file, when the agent identifies them, then it consolidates into a single section
- Given USER.md exceeds ~120 lines or AGENTS.md exceeds ~400 lines, when the maintenance agent runs, then it prunes to bring files within limits
- Given the context maintenance agent completes its work, then all file changes are committed to git — staging only `context/`
- Given context files are already clean and within size limits, when the maintenance agent runs, then it exits with no changes (idempotent)
- Given the context maintenance task runs with a non-empty skill registry, when the prompt is assembled, then it includes a skill catalog listing all skills with name, description, and absolute path, plus instructions to remove entries duplicating skill content
- Given the skill registry is unavailable or empty, when the context maintenance task runs, then the prompt contains no skill catalog section — identical to behavior before R22
- Given the context maintenance task runs, when the prompt is assembled, then it includes the `STORE_PURPOSE_SECTION` defining the authority hierarchy (R26)
- Given other information stores contain files, when the context maintenance task runs, then the prompt includes a cross-store manifest listing those files by name and path (R24)
- Given no other stores contain files, when the context maintenance task runs, then the manifest section is omitted
- Given the context maintenance task runs, when the prompt is assembled, then it includes `CONTRADICTION_DETECTION_SECTION` instructing the agent to resolve contradictions in favor of the more authoritative store (R25)

### Maintenance Configuration (R20)

Tiered time window thresholds and schedule are configurable via the `[memory.maintenance]` TOML section with sensible defaults.

**Acceptance Criteria**:
- Given the configuration file, when the user inspects the memory maintenance section, then the cron schedule, time window thresholds, and enabled toggle are present with sensible defaults
- Given custom values in configuration, when the maintenance tasks run, then they use the configured values instead of defaults

### Idempotency and Error Handling (R18, R19)

Maintenance is designed to be idempotent and handle errors gracefully. Agent failures are contained to the individual job, concurrent access is safe without locking, and git provides implicit rollback for maintenance mistakes.

**Acceptance Criteria**:
- Given the episodic maintenance task runs twice in succession, when no new conversations have occurred between runs, then the second run produces no changes
- Given the facts or preferences maintenance task runs twice in succession, when no new information has been added, then the second run produces no changes
- Given the maintenance agent fails or produces invalid output, when the error is caught, then the failure is logged and other maintenance jobs are unaffected
- Given maintenance runs while a user is actively writing memories, when a file is modified during processing, then the agent's view reflects whatever state it read — no locking is required
- Given maintenance modifies or deletes files, when the post-maintenance git commit runs, then the changes are committed
