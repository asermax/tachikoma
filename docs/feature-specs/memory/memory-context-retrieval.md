# Memory Context Retrieval

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A per-message context provider that searches stored memories for information relevant to the current user message (formerly ran only on the first message of a session). Runs on every message using the per-message pre-processing pipeline. When an SDK session ID is available (subsequent messages), the provider forks the session so the search agent has full conversation context for informed relevance decisions. On the first message (no session ID yet), it operates as a standalone query. Returns one context entry per relevant memory file with metadata identifying the file path for deduplication. DLT-009 (embedding-based semantic search) is a potential future upgrade to the retrieval mechanism.

## User Stories

- As a user, I want my assistant to automatically recall relevant past conversations, known facts, and preferences so that responses are contextually aware without me having to repeat myself
- As a user, I want relevant memories to surface throughout the conversation as the topic evolves, not just at the start

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Search stored memories for information relevant to the current user message, on every message |
| R1 | Use an agent-based approach with file search tools to explore stored memory directories |
| R2 | Return one context entry per relevant memory file, each with the "memories" tag and metadata identifying the file path |
| R3 | If no relevant memories are found (including when directories are empty), return no context without error |
| R4 | Errors during memory search are caught and logged — never propagated to block the message |
| R5 | When an SDK session ID is available, fork the session so the search agent has full conversation context to make informed relevance decisions |
| R6 | Check existing context entries' metadata to skip memories already present — never remove, only append |
| R7 | Validate agent-returned file paths to ensure they are within the memories/ directory |

## Behaviors

### Memory Search (R0, R1)

The provider searches stored memories by exploring the memory directories using file search tools, finding content relevant to the user's message. Runs on every message via the per-message pipeline.

**Acceptance Criteria**:
- Given a user message related to previously stored memories, when the memory provider runs, then it searches memory directories for relevant files
- Given the memory provider runs, when it searches, then it explores `memories/episodic/`, `memories/facts/`, and `memories/preferences/` directories
- Given the memory provider runs on every message, when a follow-up message introduces a new topic, then it searches for memories relevant to the new topic
- Given the memory provider runs on every message, when a follow-up message continues the same topic, then the agent can decide no new memories are needed

### Per-File Context Entries (R2)

Results are returned as one context entry per relevant memory file, each with the "memories" tag and metadata identifying the file path. The provider reads the file directly from disk.

**Acceptance Criteria**:
- Given the memory provider finds N relevant memories, when it returns results, then it produces N individual context entries, each with tag "memories" and metadata `{"memory_path": "<path>"}`
- Given each context entry, then it contains the full file content of the memory file

### No Relevant Memories (R3)

When no relevant memories exist, the provider returns nothing and the message proceeds unmodified.

**Acceptance Criteria**:
- Given a user message with no related memories, when the memory provider runs, then it returns no context (None)
- Given the memories directory is empty, when the memory provider runs, then it returns no context without error

### Error Handling (R4)

Provider errors are isolated — they never block the conversation.

**Acceptance Criteria**:
- Given the memory search agent fails (e.g., SDK connection error, fork failure), when the provider catches the error, then it logs the failure and returns None
- Given the memory search agent exhausts its turn limit without producing a result, when the provider processes the response, then it returns None

### Session Forking (R5)

When conversation context is available, the provider forks the current session so the search agent can make informed decisions about whether new memories are needed.

**Acceptance Criteria**:
- Given a session with an existing SDK session ID, when the memory provider runs, then it forks the session so the search agent has full conversation context
- Given the first message of a new session (no SDK session ID yet), when the memory provider runs, then it operates as a standalone query without forking

### Deduplication (R6)

Already-loaded memories are skipped to prevent duplicate injection.

**Acceptance Criteria**:
- Given a memory file is already in existing context entries (matched by metadata), when the provider processes results, then it skips that memory
- Given new memory entries are produced, when they are persisted, then they are appended alongside existing entries — no existing entries are removed
- Given there are no existing memory entries, when the provider returns results, then all returned memories are added

### Path Validation (R7)

Agent-returned file paths are validated to prevent reading files outside the memory directory.

**Acceptance Criteria**:
- Given the agent returns a file path outside the memories/ directory, when the provider validates it, then it rejects the path and logs a warning
- Given the agent returns a path with directory traversal (e.g., `../`), when the provider resolves it, then it rejects the path
