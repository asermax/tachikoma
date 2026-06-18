# Memory

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The `memory` extension (`src/extensions/memory/`) maintains long-term memory as git-versioned markdown under the workspace `memories/` directory, organized into two stores — **episodic** (date-stamped daily/weekly/monthly conversation summaries) and **topics** (everything known about a subject — stable reference facts and the user's subjective preferences together, one topic per file) — plus raw transcript archives. A static index of the topics store is injected once per session as a context section (main and background). Extraction and maintenance run as a single phased **trunk-close pipeline** at trunk close: each unextracted conversation branch is forked so the same assistant folds it into the stores (tool-limited to file edits), then marker-guarded prune, consolidate, and core-context passes consolidate and prune the stores. A one-time, self-detecting migration folds any legacy `facts/` and `preferences/` stores into `topics/` with no data loss. Transcript archives are pruned by age on a cron.

Memory agents have no delete tool: files to be removed are emptied by the agent and swept by the host afterwards. The topics store carries a `MEMORY.md` index mapping filenames to one-line descriptions, kept in sync at write time and rebuilt weekly. The per-session `core-context` processor is owned by the context extension (see [foundational-context](./foundational-context.md)); the periodic *maintenance* of those context files runs as a step in the memory trunk-close pipeline.

## User Stories

- As the system, I need learnings from completed conversations persisted automatically so that future sessions are aware of past interactions and subjects
- As a user, I want memories stored as plain markdown in my workspace so that I can inspect, edit, and version them
- As the system, I need periodic consolidation so that the store doesn't degrade from accumulated staleness, fragmentation, and verbosity

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The `init-memory-layout` bootstrap hook creates `memories/episodic/`, `memories/topics/`, and `memories/transcripts/` idempotently (no `facts/` or `preferences/` is created) |
| R1 | Bootstrap seeds `topics/MEMORY.md`: header-only when the directory is empty, placeholder entries (`Description pending update`) with Title-Case names for pre-existing files, and leaves an existing index untouched |
| R2 | A `migrate-memory-stores` bootstrap hook (registered before `init-memory-layout`) folds legacy `facts/` + `preferences/` files into merged `topics/` files (same-subject files across both old stores merge into one; distinct subjects stay distinct) with no data loss — the agent only writes under `topics/` and the host removes the old stores outright after committing the fold, so the pre-migration state is git-recoverable; it is self-detecting and idempotent — a no-op on fresh installs and already-migrated workspaces (detection keys on legacy-store non-empty markdown content, not `topics/` presence — an empty, whitespace-only, or unreadable old file does not count) |
| R3 | A `memories` context section (registered via `app.agent.use(provideContext(() => buildMemoryContext(root), "memories"), { sessionScopes: ["main", "background"] })`) is injected once per session in both main and background as a hidden message: a layout section describing the stores (including the read-only/post-processing note) plus a single `## Topics Index`; it yields empty content (no injection) when `memories/` does not exist |
| R4 | Index entries must match `[Name](./file.md): description`; malformed lines are skipped, a header-only index contributes no index section while the layout section is still injected |
| R5 | Extraction and maintenance run as a single `memory-trunk-close` post-processor (`main` phase, registered only when `maintenance.enabled`) that drives a strictly-ordered, marker-guarded pipeline at trunk close: per-branch extraction → prune → consolidate → core-context; a background run with no trunk is a no-op |
| R6 | Per-branch extraction forks each branch lacking an `extracted` marker — `app.agent.forkAndContinue(branchFile, instruction, "processor", FILE_EDIT_TOOLS)` (the neutral file-tool allowlist in `src/agent/file-tools.ts`), backed by `SessionManager.forkFrom` — so the extraction agent is the same assistant with that branch's turns live in its history and the composed persona intact; the source transcript is never mutated |
| R7 | The fork is hard-limited to file tools (`read`, `grep`, `find`, `ls`, `edit`, `write`) — the conversation's messaging/notification/task tools are filtered out — and the store instruction carries a silent-background directive (no chat reply, no messaging/task tools, only its store's files) |
| R8 | A branch that cannot be forked is skipped; the forked agent itself decides when a branch yields nothing worth recording |
| R9 | Each extraction's instruction is a follow-up user turn to the same assistant ("We just finished the conversation above. Using what you already know …"), with `$WORKSPACE` and `{date}` substituted — not a persona-resetting system prompt |
| R10 | Episodic extraction writes exactly one local-date file per day (`YYYY-MM-DD.md`), merging into an existing day file and folding variant-named files back into the canonical one; trivial branches may produce nothing |
| R11 | Topics extraction folds BOTH factual and preferential signals from the branch into topic files: it searches for overlap before creating files, uses broad future-mergeable topic filenames, consolidates at write time, prunes contradicted/stale entries, dedups against context files, and keeps files under ~50 lines. There is no facts-vs-preferences classification self-check — the agent identifies the subject and consolidates everything known about it into one topic |
| R12 | Topics extraction and maintenance prompts include the shared sections from `src/extensions/memory/prompts.ts`: store purpose/authority hierarchy, context-file and skill deduplication, workspace claim validation, and index update rules (the facts-vs-preferences classification-examples section is removed) |
| R13 | Extraction and maintenance agents are scoped by prompt to write only within their target store directory; deletion is expressed by emptying a file |
| R14 | After each extraction and maintenance run, `sweepEmptyMarkdown` removes empty or whitespace-only `.md` files from the target store, ignoring missing directories and non-markdown files |
| R15 | A `transcript-archive` post-processor (`finalize` phase) copies the pi session JSONL to `memories/transcripts/<pi-session-id>.jsonl` (id from the JSONL header, falling back to the source filename); it never throws — failures are logged warnings |
| R16 | Each trunk-close phase commits its workspace edits to git via `commitChanges` (the workspace commit agent groups them, `chore(memory): <store>/context maintenance` as the deterministic fallback) so each phase leaves a clean committed state; the transcript-prune cron does not commit |
| R17 | The prune phase runs one topics maintenance pass — staleness, redundancy, overlap, cluster consolidation (3+ files sharing a subject merge into one broad file), ~50-line size enforcement, context-file reconciliation, prune resolved incidents — plus the time-tiered episodic pass; no facts-vs-preferences misclassification detection runs (there is no second store to misclassify into) |
| R18 | Episodic maintenance applies configurable time tiers: clean recent dailies (default 15 days), consolidate into weekly `YYYY-WNN.md` (to 3 months), then monthly `YYYY-MM.md` (to 12 months), delete beyond that |
| R19 | Topics maintenance includes light index-consistency instructions on weekdays and a full `MEMORY.md` rebuild on Sundays (decided by an injectable clock at tick time); episodic maintenance includes no index section. The consolidate phase is an interim marker-guarded no-op pending richer phased consolidation (DLT-173) |
| R20 | Store-maintenance prompts include the store-purpose section, a names-only cross-store manifest (the other memory store plus existing root context files, for cross-store reconciliation; omitted when nothing else exists), contradiction-detection instructions under the `Skills > Topics > Context` authority hierarchy, and the scope section |
| R21 | The core-context step reviews `SOUL.md`, `USER.md`, `AGENTS.md` at the workspace root for staleness, redundancy, overlap, and size (USER.md ~120 lines, AGENTS.md ~400 lines); it is cleanup-only (adds no new content — that is the per-session `core-context` processor's job), especially conservative for SOUL.md, runs no `sweepEmptyMarkdown` (edits in place, never empties), and its prompt carries the store-purpose section plus a names-only topics manifest so context defers to the more authoritative topics store (omitted when topics is empty) |
| R22 | A `memory-transcripts-prune` cron deletes `.jsonl` files in `memories/transcripts/` whose modification time (set at archive write) is strictly older than `transcriptRetentionDays` (default 90); it is deterministic host-side deletion (no headless agent run), skips non-`.jsonl` files, never throws (missing dir is a silent no-op; per-file failures warn and continue), and is disabled — retaining transcripts forever — when `transcriptRetentionDays <= 0` (zero or any negative value) |
| R23 | Configuration lives under `[extensions.memory]`: `enabled` and `maintenance` (`enabled`, `recentDays`, `weeklyThresholdMonths`, `monthlyThresholdMonths`, plus `transcriptsSchedule` and `transcriptRetentionDays` for transcript retention) |
| R24 | `enabled = false` registers nothing — no bootstrap hooks, provider, processors, or crons |

## Behaviors

### Workspace Layout, Migration, and Index Seeding (R0–R2)

**Acceptance Criteria**:
- Given an empty workspace, when bootstrap runs, then `memories/` contains `episodic`, `topics`, and `transcripts` (no `facts`/`preferences`), and `topics/MEMORY.md` contains only the `# Memory Index` header
- Given pre-existing topic files without an index, when bootstrap runs, then each gets a placeholder entry with a Title Case name derived from its filename
- Given an existing `topics/MEMORY.md`, when bootstrap runs again, then its content is preserved (idempotent)
- Given a workspace with legacy `facts/`/`preferences/` files and no `topics/`, when the migration runs (once, at startup before layout), then an agent folds them into merged `topics/` files (same-subject files across both old stores merge into one; distinct subjects stay distinct), the old stores are removed outright, and the first session's injected index reflects the migrated topics
- Given the migration runs, then it commits the fold before touching the old stores (fold → commit → remove → commit), so no content is lost and the pre-migration state is git-recoverable; given an interrupted fold (legacy content still present), then it re-runs cleanly, re-merging rather than duplicating
- Given a hard agent-run error during the fold, then the hook aborts without removing, the legacy stores still hold content, and the whole pass retries next startup; given an unreadable old file, it is skipped and retried next run; given an empty old file, no topic is created for it
- Given a fresh install or an already-migrated workspace, when the migration check runs, then it is a no-op

### Static Index Injection (R3, R4)

**Acceptance Criteria**:
- Given a topics index with entries, when a message is handled, then the injected block (tag `memories`) contains the layout section (describing the topics store) and a single `## Topics Index` section with one bullet per entry; there is no separate Facts or Preferences index section
- Given a header-only index, when the provider runs, then it contributes no index section while the layout section is still injected
- Given no `memories/` directory, when the section is built, then it yields empty content and contributes nothing
- Given malformed index lines, when the index is formatted, then well-formed entries are kept and malformed ones dropped silently

### Extraction at Trunk Close (R5–R14)

**Acceptance Criteria**:
- Given a trunk closes with branches, when the `memory-trunk-close` processor runs, then each unextracted branch is forked (`forkAndContinue`) and the same assistant folds it into the stores via one follow-up instruction per store naming the absolute store directory, hard-limited to file tools — the branch's turns are already live in the fork's history, not replayed as text
- Given the fork reuses the branch session, when the allowlist is applied, then the extraction agent can only use the file tools (`read`/`grep`/`find`/`ls`/`edit`/`write`); the conversation's messaging/notification/task tools are filtered out, and the silent-background directive reinforces this
- Given a branch already carrying an `extracted` marker, when the phase re-runs (e.g. after a crash), then it is skipped (extraction is idempotent per branch)
- Given the fork runs, when the extraction agent acts, then it sends no user-facing message, asks no question, and uses only file tools (the silent-background directive reinforces the allowlist)
- Given a background/headless run with no trunk, when the `memory-trunk-close` processor runs, then it no-ops (there are no day's branches to fold)
- Given the agent emptied a file (merge or obsolescence), when the run completes, then the sweep deletes it while non-empty files survive

### Topics Extraction Folds Both Signal Types (R11, R12)

**Acceptance Criteria**:
- Given a closed branch where the user stated a stable fact and a related preference about the same subject, when extraction runs, then both signals land in the same topic file (create-or-merge); no classification self-check runs and nothing is routed between stores; `topics/MEMORY.md` is updated once
- Given no existing topic matches the branch's new content, when extraction runs, then it creates a new broad-topic file (broad, future-mergeable name) rather than appending to an unrelated topic
- Given information that duplicates content already in a root context file (SOUL/USER/AGENTS) or an existing topic, when extraction runs, then it updates/merges rather than duplicating, and prunes entries contradicted by the conversation

### Transcript Archival (R15)

**Acceptance Criteria**:
- Given a transcript whose header line is `{"type":"session","id":"<id>",…}`, when the archive processor runs, then the file is copied verbatim to `memories/transcripts/<id>.jsonl`, recreating the directory if needed
- Given a transcript without a session header id, when archived, then the destination uses the source filename
- Given a missing source file or copy failure, when the processor runs, then it logs a warning and completes without throwing; a null transcript path skips with a debug log

### Trunk-Close Maintenance (R16–R21)

**Acceptance Criteria**:
- Given the prune phase runs, then a single topics maintenance pass evaluates staleness, redundancy, overlap, cluster fragmentation (3+ files sharing a subject merge into one broad file), and size limits across the topics store; no facts-vs-preferences misclassification detection runs
- Given a topic duplicates or contradicts content in a root context file, when maintenance runs, then it trims to a pointer or reconciles per the authority hierarchy (`Skills > Topics > Context`)
- Given the topics maintenance step runs Monday–Saturday, then its prompt includes the light index-consistency section; on Sunday it includes the full rebuild section instead
- Given the consolidate phase runs, then it is a marker-guarded no-op (richer phased consolidation is deferred to DLT-173)
- Given the core-context step runs, then it reviews SOUL/USER/AGENTS, is cleanup-only and especially conservative for SOUL.md, carries no index instructions and runs no sweep; given the topics store holds files, its prompt embeds a names-only topics manifest so context defers to topics
- Given a phase completes, then its workspace edits are committed to git via `commitChanges`; the transcript-prune cron performs no commit

### Transcript Retention (R22)

**Acceptance Criteria**:
- Given the transcript-prune cron runs with `transcriptRetentionDays = 90`, then `.jsonl` files in `memories/transcripts/` whose mtime is strictly older than 90 days are deleted, files at exactly the threshold and newer survive, non-`.jsonl` files are left untouched, and eligibility follows mtime rather than the filename
- Given the cron runs with `transcriptRetentionDays = 0` (or any negative value), then no transcript is deleted; given the transcripts directory is missing, then the tick completes silently; given one entry cannot be removed, then a warning is logged and the remaining files are still pruned

### Configuration (R23, R24)

**Acceptance Criteria**:
- Given no `[extensions.memory]` section, when the extension loads, then defaults apply: enabled, maintenance enabled with thresholds 15 days / 3 months / 12 months, transcripts schedule `50 3 * * *`, and `transcriptRetentionDays` 90
- Given `maintenance.enabled = false`, when the extension loads, then the trunk-close pipeline is not registered, but the `transcript-archive` processor and `memory-transcripts-prune` cron still register (transcript retention is independent of memory maintenance)
- Given `enabled = false`, when the extension loads, then setup logs the disabled state and registers nothing
