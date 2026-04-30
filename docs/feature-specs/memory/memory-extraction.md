# Memory Extraction

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

After a conversation ends, the system automatically extracts and persists learnings as structured markdown files. Each memory type has its own processor that forks the original SDK session and directs the agent to read existing memories, analyze the conversation, and create, update, or delete memory files as needed. Three memory types: episodic (date-stamped conversation summaries), facts (named files about the user and other factual information), and preferences (named files about how the user likes things). All memories are human-readable markdown in the workspace.

The memory workspace also includes `memories/transcripts/`, a non-extractive subdirectory populated by a transcript archive processor that copies each conversation's SDK transcript into the workspace on session close. Archived transcripts are raw `.jsonl` files (not curated markdown) and are named by SDK session ID so the archived path is derivable without a dedicated model field.

## User Stories

- As the system, I need to automatically extract and persist learnings from completed conversations so that future sessions are contextually aware of past interactions, known preferences, and prior decisions
- As a user, I want my memories stored as readable markdown files so that I can inspect, understand, and edit them directly

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

Named files about the user and other stable reference information — personal details, key people, technical decisions, routines — updated when new information emerges.

**Acceptance Criteria**:
- Given a conversation where new factual information is revealed, when the facts processor runs, then the forked agent searches existing files for topic overlap and updates the matching file, or creates a new topic-named file in `memories/facts/` only when no existing file covers the topic
- Given previously stored factual information is contradicted, when the facts processor runs, then the agent updates the existing file with corrected information
- Given a previously stored fact becomes invalid, when the facts processor runs, then the agent may delete the obsolete file
- Given information about a topic is spread across multiple files, when the facts processor runs, then the agent merges them into a single file and deletes the redundant ones
- Given a facts file contains entries contradicted by the conversation, when the facts processor runs, then the agent updates or removes the stale entries (R10)
- Given ambiguous signals ("I might switch..."), when the facts processor runs, then it retains existing entries unchanged — only clear evidence triggers pruning (R10)

### Preferences Memories (R3)

Named files about how the user likes things — code style, communication, workflow — updated or deleted when preferences change.

**Acceptance Criteria**:
- Given a conversation where the user expresses a preference, when the preferences processor runs, then the forked agent searches existing files for topic overlap and updates the matching file, or creates a new topic-named file in `memories/preferences/` only when no existing file covers the topic
- Given a user changes a previously expressed preference, when the preferences processor runs, then the agent updates or deletes the existing file
- Given a conversation with no preference-related content, when the preferences processor runs, then no changes are made
- Given the same preference appears in multiple files, when the preferences processor runs, then the agent consolidates into the most specific file and removes duplicates
- Given a preferences file contains entries the user has reversed or moved away from, when the preferences processor runs, then the agent updates or removes the stale entries (R10)
- Given ambiguous signals ("I might try..."), when the preferences processor runs, then it retains existing entries unchanged — only clear evidence triggers pruning (R10)

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
