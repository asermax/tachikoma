# Memory Extraction

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

After a conversation ends, the system automatically extracts and persists learnings as structured markdown files. Each memory type has its own processor that forks the original SDK session and directs the agent to read existing memories, analyze the conversation, and create, update, or delete memory files as needed. Three memory types: episodic (date-stamped conversation summaries), facts (named files about the user and other factual information), and preferences (named files about how the user likes things). All memories are human-readable markdown in the workspace.

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
| R4 | Memories organized in subdirectories: `memories/episodic/`, `memories/facts/`, `memories/preferences/` |
| R5 | Memory files are human-readable markdown, directly inspectable and editable |
| R6 | Bootstrap hook creates the memory directory structure on first run (idempotent) |

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
- Given a completed conversation with meaningful content, when the episodic processor runs, then the forked agent creates or updates a date-stamped file (`YYYY-MM-DD.md`) in `memories/episodic/`
- Given multiple conversations on the same day, when the episodic processor runs, then the agent consolidates entries for that day rather than creating duplicates
- Given a trivial conversation, when the episodic processor runs, then the agent may determine there's nothing meaningful to record — no file creation is forced

### Facts Memories (R3)

Named files about the user and other factual information — job, projects, important dates, routines — updated when new information emerges.

**Acceptance Criteria**:
- Given a conversation where new factual information is revealed, when the facts processor runs, then the forked agent creates or updates a topic-named file in `memories/facts/`
- Given previously stored factual information is contradicted, when the facts processor runs, then the agent updates the existing file with corrected information
- Given a previously stored fact becomes invalid, when the facts processor runs, then the agent may delete the obsolete file

### Preferences Memories (R3)

Named files about how the user likes things — code style, communication, workflow — updated or deleted when preferences change.

**Acceptance Criteria**:
- Given a conversation where the user expresses a preference, when the preferences processor runs, then the forked agent creates or updates a topic-named file in `memories/preferences/`
- Given a user changes a previously expressed preference, when the preferences processor runs, then the agent updates or deletes the existing file
- Given a conversation with no preference-related content, when the preferences processor runs, then no changes are made

### Directory Structure and Bootstrap (R4, R6)

**Acceptance Criteria**:
- Given no `memories/` directory exists, when the memory bootstrap hook runs, then `memories/`, `memories/episodic/`, `memories/facts/`, and `memories/preferences/` are created
- Given the directory structure already exists, when the hook runs, then nothing changes (idempotent)

### Human Readability (R5)

**Acceptance Criteria**:
- Given memory files exist, when the user navigates to `memories/`, then they can read and understand the contents in any text editor
- Given a user manually edits a memory file, when the next extraction runs, then the agent sees and respects the edits
