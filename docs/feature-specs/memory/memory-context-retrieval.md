# Memory Context Retrieval

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A context provider that searches stored memories for information relevant to the current user message. Uses an agent-based approach with file search tools to explore stored memories (`memories/episodic/`, `memories/facts/`, `memories/preferences/`), returning a ranked list of the most relevant memory files with short summaries. The provider gives the main agent pointers to read further if necessary. DLT-009 (embedding-based semantic search) is a potential future upgrade to the retrieval mechanism.

## User Stories

- As a user, I want my assistant to automatically recall relevant past conversations, known facts, and preferences so that responses are contextually aware without me having to repeat myself

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Search stored memories for information relevant to the current user message |
| R1 | Use an agent-based approach with file search tools to explore stored memory directories |
| R2 | Return a ranked list of the most relevant memory files (targeting up to 10) with a short summary for each, plus instructions for the main agent to read further if necessary |
| R3 | If no relevant memories are found (including when directories are empty), return no context without error |
| R4 | Errors during memory search are caught and logged — never propagated to block the message |

## Behaviors

### Memory Search (R0, R1)

The provider searches stored memories by exploring the memory directories using file search tools, finding content relevant to the user's message.

**Acceptance Criteria**:
- Given a user message related to previously stored memories, when the memory provider runs, then it searches memory directories for relevant files
- Given the memory provider runs, when it searches, then it explores `memories/episodic/`, `memories/facts/`, and `memories/preferences/` directories
- Given the memory provider's agent explores memory files, when it finds relevant content, then it returns a concise ranked list of relevant files with summaries (not raw file dumps)

### Context Result Format (R2)

Results are returned as a context block with the "memories" tag, containing a ranked list of relevant files.

**Acceptance Criteria**:
- Given the memory provider finds relevant memories, when it returns a result, then the context block uses the "memories" tag
- Given the memory provider returns results, when the context is injected, then it includes instructions telling the main agent to read the referenced files if it needs more detail

### No Relevant Memories (R3)

When no relevant memories exist, the provider returns nothing and the message proceeds unmodified.

**Acceptance Criteria**:
- Given a user message with no related memories, when the memory provider runs, then it returns no context (None)
- Given the memories directory is empty, when the memory provider runs, then it returns no context without error

### Error Handling (R4)

Provider errors are isolated — they never block the conversation.

**Acceptance Criteria**:
- Given the memory search agent fails (e.g., SDK connection error), when the provider catches the error, then it logs the failure and returns None
- Given the memory search agent exhausts its turn limit without producing a result, when the provider processes the response, then it returns None
