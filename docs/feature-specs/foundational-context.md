# Foundational Context

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `context` extension (`src/extensions/context/`) composes the agent's identity from workspace-root files: SOUL.md (personality) and USER.md (durable user knowledge) are combined with the core-owned main-session base prompt (`buildMainSystemPrompt`, see [DES-005](../design/DES-005-base-prompt-ownership.md)) into the pi session's system prompt. AGENTS.md is discovered natively by pi from the workspace root and needs no handling in the extension.

A companion `core-context` post-processor (`src/extensions/context/processor.ts`, currently registered by the [memory](./memory.md) extension) analyzes each closed conversation and conservatively updates the three files, staging ambiguous signals in a pending-signals file so recurring patterns can be promoted later. Separately, a periodic foundational-context maintenance tick owned by the [memory](./memory.md) extension (`memory-context-maintenance`) does a cleanup-only whole-file pass over the three files, pruning staleness and bloat without adding new content.

## User Stories

- As a user, I want the assistant's personality, knowledge of me, and operating instructions to live in plain markdown files so that I can read and edit them directly
- As the system, I need foundational context to evolve from conversations automatically so that identity files stay current without manual edits

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | SOUL.md and USER.md at the workspace root are read during bootstrap; missing files are created from built-in templates |
| R1 | The system prompt is composed as identity + SOUL.md + shared operational/interactive guidance + USER.md + a workspace-root line via the core `buildMainSystemPrompt` builder ([DES-005](../design/DES-005-base-prompt-ownership.md)), registered through `app.agent.systemPrompt` and replacing pi's default coding prompt; the interactive guidance covers working hygiene, caution with irreversible/outward-facing actions, and awareness that focused sub-tasks can be delegated via `delegate_to_agent` |
| R2 | The composed prompt is re-evaluated on every session open, re-reading SOUL.md/USER.md from disk each time (the bootstrap-time contents are only a fallback when a read fails), so edits — including the processor's own — take effect on the next session without a restart |
| R3 | AGENTS.md is not composed by the extension — pi discovers it natively from the workspace root when a session opens |
| R4 | A `core-context` post-processor (`preFinalize` phase) forks the just-ended pi session (`app.agent.forkAndContinue(piSessionFile, instruction, "processor", MEMORY_FILE_TOOLS)`) so the same assistant — full conversation live in its history, persona intact — updates SOUL.md, USER.md, and AGENTS.md; the source transcript is never mutated |
| R5 | The processor skips (no fork) when the session has no transcript (`piSessionFile` is null) |
| R6 | Ambiguous signals are staged in `{dataDir}/pending-signals.md` as dated markdown entries (`- **YYYY-MM-DD**: text`); the current entries are injected into the processor's follow-up instruction as a numbered snapshot (S1..Sn) |
| R7 | Pending signals older than 30 days are cleared before each run; the file is deleted when every entry has expired; unparseable content is left alone with a warning |
| R8 | The update policy is prompt-encoded and conservative: clear evidence updates files directly, ambiguous signals are staged, semantically recurring staged signals are promoted and removed, corrections are extracted as positive instructions, stale content is pruned, and size limits are enforced (USER.md ~120 lines, AGENTS.md ~400 lines) |
| R9 | The fork is hard-limited to file tools (`read`, `grep`, `find`, `ls`, `edit`, `write`) on the `processor` tier — the allowlist also filters out the conversation's messaging/notification/task tools — and a silent-background directive instructs it to modify only the three context files and the pending signals file, verifying workspace claims before writing them; it may read anywhere in the workspace |
| R10 | The processor registration lives in the memory extension's setup — `[extensions.memory] enabled = false` also disables core context updates |
| R11 | A periodic foundational-context maintenance tick (cleanup-only) reviews the three files for staleness, redundancy, overlap, and size on a staggered cron; it adds no new content, is especially conservative for SOUL.md, and is owned by the memory extension's maintenance wiring — see [memory](./memory.md) R20a for the full contract |

## Behaviors

### System Prompt Composition (R0, R1, R2, R3)

The extension's bootstrap hook (`load-context-files`) reads or creates the two files; the registered builder re-reads them from disk on each build and calls the core `buildMainSystemPrompt` (which combines the identity, the shared operational/interactive guidance, the SOUL/USER contents, and the workspace-root line). The agent manager composes all registered builders on each session open (see [agent-integration](./agent-integration.md)).

**Acceptance Criteria**:
- Given a workspace without SOUL.md or USER.md, when bootstrap runs, then each missing file is created from its template and that template content is used in the prompt
- Given existing SOUL.md/USER.md files, when bootstrap runs, then their contents are used verbatim and the files are not modified
- Given a non-ENOENT read error, when bootstrap runs, then the error propagates and startup aborts
- Given the agent manager opens a conversational session, then the system prompt contains the identity, the shared operational/interactive guidance (including delegate-awareness), SOUL.md content, USER.md content, and the workspace root path, replacing pi's coding prompt
- Given SOUL.md or USER.md is edited while the process runs, when the next session opens, then the prompt reflects the edited file contents (each is re-read from disk per build; the startup snapshot is only the fallback when a read fails); AGENTS.md is likewise re-read natively by pi on each session open

### Core Context Update Run (R4, R5, R9, R10)

`createCoreContextProcessor` cleans and snapshots pending signals, fills the instruction template (`{date}`, `$WORKSPACE`, `$SIGNALS_FILE`, the signals section), then forks the just-ended session and hands the same assistant that instruction as a follow-up turn under a file-tool allowlist.

**Acceptance Criteria**:
- Given a closed session with a transcript, when the processor runs, then a single fork is issued (`forkAndContinue`) on the `processor` tier with the file-tool allowlist and a follow-up instruction naming the absolute paths of SOUL.md, USER.md, and AGENTS.md — the conversation is already live in the fork's history, not replayed in the prompt
- Given a session without a transcript (`piSessionFile` is null), when the processor runs, then no fork is issued
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
