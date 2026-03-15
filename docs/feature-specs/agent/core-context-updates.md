# Core Context Updates

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

After a conversation ends, a post-processing processor analyzes the completed conversation for information that should update the assistant's three foundational context files: SOUL.md (personality and behavioral traits), USER.md (user knowledge and personal information), and AGENTS.md (operational instructions and workflow preferences). Clear, explicit signals update the files directly; ambiguous signals are staged in a pending signals file and promoted to context file updates only when a recurring pattern is detected. The processor is conservative — these files carry higher weight than individual memories, so only high-confidence changes are applied.

This is distinct from memory extraction: memory processors create individual files for retrieval (facts, preferences, episodic summaries), while this processor updates the foundational documents that shape the assistant's identity and behavior across all interactions.

## User Stories

- As the system, I need to automatically update foundational context files from conversation learnings so that the assistant's personality, user knowledge, and operational instructions evolve naturally over time without requiring manual edits

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Analyze completed conversations and update foundational context files (SOUL.md, USER.md, AGENTS.md) when clear evidence warrants it |
| R1 | Detect user information changes (new job, location, projects, etc.) and update USER.md |
| R2 | Detect personality/behavioral feedback ("be more concise", "stop using emojis") and update SOUL.md |
| R3 | Detect operational instruction changes ("always use pytest", "prefer JSON over YAML") and update AGENTS.md |
| R4 | Conservative update policy — only apply changes with clear conversational evidence; never overwrite correct info with noise |
| R5 | Route updates to the correct context file based on information type |
| R6 | Read-first strategy — read each file before modifying, preserving existing structure and merging changes contextually |
| R7 | No-op when no context-file-relevant content in conversation |
| R8 | Plug into the post-processing pipeline as a standard PostProcessor (main phase) |
| R9 | Pending signals staging mechanism for ambiguous signals |
| R9.1 | Structured format with entry date, stored at `.tachikoma/pending-signals.md` |
| R9.2 | Agent accesses pending signals only via provided MCP tools (read and add) — no direct file access; tool design reinforces this constraint |
| R9.3 | Processor auto-clears entries older than 1 month on every run |
| R9.4 | On recurrence detection, agent promotes the signal to a context file update; the original entry naturally ages out |
| R10 | Log which files were updated for observability |
| R11 | Clear boundary with memory extraction — context files are foundational identity documents while memory files are individual entries for retrieval; overlap is acceptable since they serve different purposes |

## Behaviors

### Context File Updates (R0, R1, R2, R3, R5)

The processor detects conversation learnings and routes them to the appropriate context file.

**Acceptance Criteria**:
- Given a conversation where the user explicitly states new personal information (e.g., "I just started a new job at Acme Corp"), when the processor runs, then USER.md is updated to reflect the new information
- Given a conversation where the user gives explicit behavioral feedback (e.g., "from now on, always keep your responses under 3 paragraphs"), when the processor runs, then SOUL.md is updated with the personality adjustment
- Given a conversation where the user establishes an operational instruction (e.g., "always use poetry for Python projects"), when the processor runs, then AGENTS.md is updated with the new instruction
- Given a conversation containing information relevant to multiple context files, when the processor runs, then each piece of information is routed to the correct file

### Conservative Update Policy (R4, R6)

Only high-confidence changes with clear conversational evidence are applied. Ambiguous signals are staged for recurrence detection.

**Acceptance Criteria**:
- Given a conversation with an ambiguous, one-off comment (e.g., "that was too verbose"), when the processor runs, then no context file is updated — the signal goes to the pending signals file instead
- Given a context file with existing content, when the processor applies an update, then the existing structure and formatting are preserved — only the relevant section is modified
- Given a conversation where the user mentions something that contradicts existing context file content with clear evidence (e.g., "I moved from Buenos Aires to Berlin"), when the processor runs, then the outdated information is replaced with the new information
- Given a conversation with casual or hypothetical statements (e.g., "I might switch to Vim someday"), when the processor runs, then no context file updates are applied

### No-Op Behavior (R7)

**Acceptance Criteria**:
- Given a conversation with no context-file-relevant content (e.g., a purely technical debugging session), when the processor runs, then no context files are modified and no pending signals are added
- Given a conversation where the processor determines nothing warrants an update, when it completes, then it finishes cleanly without errors

### Pipeline Integration (R8)

**Acceptance Criteria**:
- Given the processor is registered in the pipeline's main phase, when a session closes with a valid SDK session ID, then the processor runs alongside other main-phase processors
- Given the processor fails during execution, when other processors are running, then the failure is logged and other processors complete normally (error isolation)
- Given a session closes without an SDK session ID, when the pipeline would run, then the processor is not invoked
- Given context files are updated by the processor, when the finalize phase runs, then the existing git post-processor commits the changes automatically (no additional commit logic needed)

### Context Freshness (R8)

**Acceptance Criteria**:
- Given context files are updated after a session closes, when the next session starts, then the coordinator loads the updated context files — changes do not take effect mid-conversation, only on the next session

### Pending Signals — Staging (R9, R9.1, R9.2)

**Acceptance Criteria**:
- Given a conversation with an ambiguous signal, when the processor determines it's not confident enough for a direct update, then the signal is added to `.tachikoma/pending-signals.md` via the add tool with the current date
- Given the pending signals file exists with entries, when the agent needs to check for recurrence, then it reads the full list (with dates) via the read tool
- Given a new ambiguous signal is semantically similar to one or more existing entries in the pending signals file, when the processor runs, then the agent promotes the signal to a context file update (recurrence detection is LLM-judgment-based using semantic similarity, not exact matching)
- Given the agent interacts with the pending signals file, then it uses only the provided read and add MCP tools — no direct file modification or deletion

### Pending Signals — Auto-Cleanup (R9.3)

**Acceptance Criteria**:
- Given the pending signals file contains entries older than 1 month, when the processor runs, then those entries are automatically cleared before the forked session begins
- Given all entries in the pending signals file are newer than 1 month, when the processor runs, then no entries are removed
- Given the pending signals file does not exist or is empty, when the processor runs, then cleanup is a no-op

### Boundary with Memory Extraction (R11)

**Acceptance Criteria**:
- Given the facts processor extracts "user works at Acme Corp" into `memories/facts/`, when the context update processor runs for the same conversation, then it may also update USER.md with the same information — duplication across systems is acceptable since they serve different purposes (retrieval vs foundational context)

### Observability (R10)

**Acceptance Criteria**:
- Given the processor updates a context file, when the update completes, then a log entry records which file was updated
- Given the processor adds a pending signal, when the entry is added, then a log entry records the signal description

### Edge Cases

**Acceptance Criteria**:
- Given a context file does not exist (e.g., was deleted after bootstrap), when the processor tries to read it, then it handles the absence gracefully — it may create the file or skip that file's updates without crashing
- Given the pending signals file is malformed or corrupted, when the processor attempts auto-cleanup, then the error is logged and the processor continues with its other work (forked session still runs)
