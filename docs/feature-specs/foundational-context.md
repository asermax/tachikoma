# Foundational Context

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `context` extension (`src/extensions/context/`) composes the agent's identity from workspace-root files: SOUL.md (personality) and USER.md (durable user knowledge) are joined with a base prompt into the pi session's system prompt. AGENTS.md is discovered natively by pi from the workspace root and needs no handling in the extension.

A companion `core-context` post-processor (`src/extensions/context/processor.ts`, currently registered by the [memory](./memory.md) extension) analyzes each closed conversation and conservatively updates the three files, staging ambiguous signals in a pending-signals file so recurring patterns can be promoted later. Separately, a periodic foundational-context maintenance tick owned by the [memory](./memory.md) extension (`memory-context-maintenance`) does a cleanup-only whole-file pass over the three files, pruning staleness and bloat without adding new content.

## User Stories

- As a user, I want the assistant's personality, knowledge of me, and operating instructions to live in plain markdown files so that I can read and edit them directly
- As the system, I need foundational context to evolve from conversations automatically so that identity files stay current without manual edits

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | SOUL.md and USER.md at the workspace root are read during bootstrap; missing files are created from built-in templates |
| R1 | The system prompt is composed as base prompt + SOUL.md + USER.md + a workspace-root line, registered via `app.agent.systemPrompt` and replacing pi's default coding prompt |
| R2 | The composed prompt is re-evaluated on every session open, re-reading SOUL.md/USER.md from disk each time (the bootstrap-time contents are only a fallback when a read fails), so edits — including the processor's own — take effect on the next session without a restart |
| R3 | AGENTS.md is not composed by the extension — pi discovers it natively from the workspace root when a session opens |
| R4 | A `core-context` post-processor (`preFinalize` phase) runs a headless agent over the closed conversation to update SOUL.md, USER.md, and AGENTS.md |
| R5 | The processor skips when the session has no transcript or the transcript renders to an empty conversation |
| R6 | Ambiguous signals are staged in `{dataDir}/pending-signals.md` as dated markdown entries (`- **YYYY-MM-DD**: text`); the current entries are injected into the processor's system prompt as a numbered snapshot (S1..Sn) |
| R7 | Pending signals older than 30 days are cleared before each run; the file is deleted when every entry has expired; unparseable content is left alone with a warning |
| R8 | The update policy is prompt-encoded and conservative: clear evidence updates files directly, ambiguous signals are staged, semantically recurring staged signals are promoted and removed, corrections are extracted as positive instructions, stale content is pruned, and size limits are enforced (USER.md ~120 lines, AGENTS.md ~400 lines) |
| R9 | The headless run gets file tools (`read`, `grep`, `find`, `ls`, `edit`, `write`) on the `processor` tier; it may read anywhere in the workspace but is instructed to modify only the three context files and the pending signals file, verifying workspace claims before writing them |
| R10 | The processor registration lives in the memory extension's setup — `[extensions.memory] enabled = false` also disables core context updates |
| R11 | A periodic foundational-context maintenance tick (cleanup-only) reviews the three files for staleness, redundancy, overlap, and size on a staggered cron; it adds no new content, is especially conservative for SOUL.md, and is owned by the memory extension's maintenance wiring — see [memory](./memory.md) R20a for the full contract |

## Behaviors

### System Prompt Composition (R0, R1, R2, R3)

The extension's bootstrap hook (`load-context-files`) reads or creates the two files; the registered builder re-reads them from disk on each build and joins `BASE_PROMPT`, the SOUL and USER contents, and the workspace-root line. The agent manager composes all registered builders on each session open (see [agent-integration](./agent-integration.md)).

**Acceptance Criteria**:
- Given a workspace without SOUL.md or USER.md, when bootstrap runs, then each missing file is created from its template and that template content is used in the prompt
- Given existing SOUL.md/USER.md files, when bootstrap runs, then their contents are used verbatim and the files are not modified
- Given a non-ENOENT read error, when bootstrap runs, then the error propagates and startup aborts
- Given the agent manager opens a conversational session, then the system prompt contains the base prompt, SOUL.md content, USER.md content, and the workspace root path, replacing pi's coding prompt
- Given SOUL.md or USER.md is edited while the process runs, when the next session opens, then the prompt reflects the edited file contents (each is re-read from disk per build; the startup snapshot is only the fallback when a read fails); AGENTS.md is likewise re-read natively by pi on each session open

### Core Context Update Run (R4, R5, R9, R10)

`createCoreContextProcessor` loads the conversation from the pi transcript, cleans and snapshots pending signals, fills the prompt template (`{date}`, `$WORKSPACE`, `$SIGNALS_FILE`, the signals section), and runs a headless agent.

**Acceptance Criteria**:
- Given a closed session with a transcript, when the processor runs, then a single headless run is issued on the `processor` tier with file tools, a system prompt naming the absolute paths of SOUL.md, USER.md, and AGENTS.md, and the rendered conversation in the prompt
- Given a session without a transcript, or whose transcript renders empty, when the processor runs, then no headless run is issued
- Given the processor is registered with phase `preFinalize`, when post-processing runs, then it executes after the `main`-phase memory extraction and before the `finalize` phase (see [memory](./memory.md))
- Given `[extensions.memory] enabled = false`, when extensions load, then the processor is never registered

### Pending Signals Lifecycle (R6, R7, R8)

The agent manages the signals file directly with its file tools — appending dated lines to stage, deleting lines to promote or discard — while the host owns expiry.

**Acceptance Criteria**:
- Given pending signals exist, when the processor builds its prompt, then entries appear as `S1: **date**: text` lines and the prompt names the signals file path for direct editing
- Given no signals file or an empty one, when the prompt is built, then the section reads "No pending signals at this time."
- Given entries older than 30 days, when the processor runs, then they are removed from the file before the snapshot is taken; if all entries expired, the file is deleted
- Given a signals file with content but no parseable entries, when cleanup runs, then the file is left untouched and a warning is logged
- Given the prompt instructions, then recurring signals are promoted to context-file updates and their lines deleted, first occurrences are staged with the current date, and stale signals are deleted proactively
