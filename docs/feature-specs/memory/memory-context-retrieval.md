# Memory Context Retrieval

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Static injection of facts and preferences memory indexes into foundational context at startup. The `memory_hook` reads `MEMORY.md` index files from the facts and preferences directories during bootstrap, formats them into navigable sections, and stashes them in the bootstrap extras bag. The `context_hook` appends them to the foundational context list, where they become part of the system prompt as `<memory_index>` sections. The agent browses these indexes and reads individual files on demand via the Read tool when entries suggest relevance. Episodic memory search is not included — deferred to a future MCP tool.

## User Stories

- As a user, I want my stored facts and preferences automatically available to the assistant on every message so that responses are contextually aware without me having to repeat myself
- As the system, I need memory context loading to be fast and reliable so that every session starts with full knowledge of stored facts and preferences

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Facts and preferences MEMORY.md indexes are loaded at startup and injected as foundational context, available on every message without per-message processing |
| R1 | Agent fetches individual fact/preference files on demand via Read when the index suggests relevance |
| R2 | Episodic memory search is deferred to a future tool (DLT-105 scope) — no episodic injection |
| R3 | System preamble expanded with general episodic memory documentation (naming conventions, retention tiers, content expectations, when to consult) |
| R4 | Error handling preserved — missing/empty/malformed MEMORY.md files don't break startup or context loading |
| R5 | Background tasks also receive memory indexes in their preprocessing context |

## Behaviors

### Static Index Injection (R0, R4)

The `memory_hook` reads both `memories/facts/MEMORY.md` and `memories/preferences/MEMORY.md` during bootstrap, formats each into a navigable section with a type description and usage instructions, and stashes the results in `ctx.extras["memory_indexes"]`. The `context_hook` reads from there and appends to the foundational context list. Missing or empty files are skipped silently. Malformed entries within a valid file are skipped silently (logged at debug level); well-formed entries are still included.

**Acceptance Criteria**:
- Given a workspace with facts and preferences directories containing MEMORY.md files, when the system starts up, then both index files are read and injected as sections in the foundational context
- Given the injected index sections, when the agent receives a message, then it sees navigable lists of memory files with one-line descriptions and file paths
- Given a workspace where the facts or preferences directory has no MEMORY.md file, when the system starts up, then the injection is skipped for that directory without error or warning
- Given a workspace where MEMORY.md exists but is empty or contains only a header with no file entries, when the system starts up, then the injection is skipped for that directory without error
- Given a workspace with no memories directory at all, when the system starts up, then no memory index sections are injected and startup proceeds normally
- Given a MEMORY.md file containing entries that don't match the expected format, when the system reads it at startup, then well-formed entries are included and malformed entries are skipped silently

### On-Demand File Reading (R1)

The agent sees the indexes as browseable lists with one-line descriptions and file paths. Each injected section includes usage instructions describing how to navigate the index. When the agent determines a file may be relevant to the current conversation, it reads the full content via the Read tool.

**Acceptance Criteria**:
- Given the agent determines a fact or preference file is relevant based on the index, when it needs the full content, then it reads the file via the Read tool
- Given each injected index section, then it includes a description of the memory type and instructions for browsing entries and reading relevant files

### System Preamble (R3)

The existing `## Memories` section in the system preamble is expanded with general documentation about episodic memory. The expansion covers naming conventions (daily `YYYY-MM-DD.md`, weekly `YYYY-WNN.md`, monthly `YYYY-MM.md`), retention tier descriptions (daily = recent detail, weekly = consolidated summaries, monthly = long-term arcs), content expectations (summaries not transcripts), and guidance on when consulting episodic context is useful versus when facts/preferences suffice. No hard retention duration numbers are included.

**Acceptance Criteria**:
- Given the system preamble's Memories subsection, when it is rendered, then it includes episodic memory naming conventions (daily, weekly, monthly)
- Given the system preamble's Memories subsection, when it is rendered, then it describes retention tiers at a general level
- Given the system preamble's Memories subsection, when it is rendered, then it explains content expectations (summaries, not transcripts)
- Given the system preamble's Memories subsection, when it is rendered, then it provides guidance on when consulting episodic context is useful versus when facts/preferences suffice
- Given the system preamble's Memories subsection, then it does not include specific retention duration numbers

### Background Task Injection (R5)

Background tasks receive memory indexes through their preprocessing step, so the task prompt includes the same navigable index sections available to the main agent.

**Acceptance Criteria**:
- Given a background task being prepared for execution, when preprocessing runs, then the task prompt includes formatted memory index sections
- Given a background task with missing MEMORY.md files, when preprocessing runs, then the task proceeds with partial or no memory context without error
