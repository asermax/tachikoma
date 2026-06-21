# Design: Memory

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/memory.md](../feature-specs/memory.md)
**Status**: Current

## Purpose

Explains how long-term memory is built on pi primitives: forking each conversation branch at trunk close and continuing it as a tool-restricted follow-up turn, over three stores — episodic (narrative), topics (knowledge), and learnings (experience) — where the topics extraction fork also classifies and writes learnings so the two never duplicate.

## Problem Context

pi sessions are in-process JSONL trees stored on disk and forkable: `SessionManager.forkFrom` copies a session file's history into a fresh session without mutating the source, and `tools` on `createAgentSession` is an allowlist independent of the system prompt (see `docs/reference/pi-sdk-notes.md`). Memory extraction leans on this: at trunk close it forks each branch so the same assistant — branch turns live, persona intact — folds it into the stores, with policy (subject identification, deduplication, consolidation, validation) expressed in a follow-up user instruction. Tool discipline is enforced by a hard `FILE_EDIT_TOOLS` allowlist on the fork (the neutral allowlist in `src/agent/file-tools.ts`), not by a separate ephemeral persona.

Memory is organized as three stores, distinct in *kind*:

- **topics** (knowledge): each `<topic>.md` file holds everything known about a subject — both the stable reference facts and the user's subjective preferences — together in free-form unified prose, with one `MEMORY.md` index entry. Collapsing the former separate `facts/` (stable, objective) and `preferences/` (subjective) stores into one removes the error-prone classification/routing boundary that fragmented a single subject across two files (a stable detail in one, a related preference in the other), so recall never depends on guessing which store a piece of knowledge was filed under.
- **learnings** (experience): each `<slug>.md` file holds one theme of recurring friction — what bites and what worked — under `## Drafts` (tentative) and `## Confirmed` (permanent) sections. Experience is different in kind from knowledge: a topic says what is true; a learning says what bites, repeatedly, or what approach turned out to be a dead end. Without it every session rediscovers the same gotchas.
- **episodic** (narrative): date-stamped summaries of what happened.

The decisive structural move is the **store-set split**: learnings joins the store sets that drive directory creation, index seeding, context injection, maintenance, and cross-store manifests, but is *excluded* from the extraction-iterating set — so it is folded into the topics extraction fork rather than getting its own. That single asymmetry enforces "one agent, one decision per signal": the topics fork classifies each extracted signal inline as topic content or learning content and writes it to exactly one store, so the two knowledge/experience stores can never duplicate each other. Learnings is also orthogonal to the `Skills > Topics > Context` authority hierarchy — a learning and a topic about the same subject are different kinds of information, so neither overrides the other.

**Constraints:**
- The fork gets a hard file-tool allowlist (`FILE_EDIT_TOOLS`); there is no delete tool and no per-directory write enforcement, so emptying-a-file plus a host sweep stands in for deletion
- The fork loads the branch turns as live history — fidelity is full, but cost scales with branch length (still ×2 forks per branch: episodic + the shared topics+learnings fork; learnings is folded into the topics fork, never its own)
- Everything is registered through the extension API (DES-001) and validated without live LLM calls (DES-002/DES-003) — tests fake `forkAndContinue` and `side.run` and assert on their arguments
- The trunk-close `consolidate` phase is a marker-guarded no-op pending DLT-173's richer phased consolidation model

**Interactions:**
- The trunk-close pipeline is a single `main`-phase post-processor; the general phased post-processing mechanism (phases `main → preFinalize → finalize`, per-processor state, startup recovery) is owned by the coordinator (see [conversation-loop](./conversation-loop.md))
- The `transcript-archive` and `git-commit` processors run in `finalize` (see [conversation-loop](./conversation-loop.md) and [git-workspace](./git-workspace.md))
- Headless runs and model tiers come from the agent manager (see [agent-integration](./agent-integration.md)); the transcript-prune cron from the core scheduler (see [core-shell](./core-shell.md))

## Design Overview

`src/extensions/memory/index.ts` wires: a `migrate-memory-stores` bootstrap hook (before layout), an `init-memory-layout` bootstrap hook, a `memories` context section for index injection (`app.agent.use(provideContext(() => buildMemoryContext(root), "memories"), { sessionScopes: ["main", "background"] })`), the single `memory-trunk-close` trunk-close post-processor (when maintenance is enabled), the transcript-archive processor, and the transcript-prune cron.

```
session start  ──> memories context section ──> hidden "memories" message: layout + Topics Index + Learnings Index (once)
startup        ──> migrate-memory-stores (before init-memory-layout) ──> fold legacy facts/preferences into topics
trunk close    ──> memory-trunk-close (main phase):
                    extract: for each unextracted branch (serial), fork its stores concurrently (episodic ∥ topics+learnings; disjoint dirs)
                             topics+learnings fork classifies each signal → topics/ or learnings/ (as draft),
                             reads existing learnings to promote a matching recurring draft → Confirmed; sweep both
                    prune:   topics maintenance + learnings maintenance + time-tiered episodic (headless side-runs) ──> sweep ──> commit
                    consolidate: marker-guarded no-op (interim, pending DLT-173)
                    core-context: SOUL/USER/AGENTS cleanup-only pass ──> commit
trunk close    ──> finalize: transcript-archive | git-commit
cron           ──> pruneTranscripts ──> delete transcripts older than retentionDays (no agent)
```

Extraction iterates the **topic-filtered** trunk branch enumeration — summarized side tangents (`kind: "tangent"`) and reversed summaries are excluded (a tangent's content is already in the main-line summary; a reversed summary is a decision the user undid via `/rollback`), so only genuine topics are folded into memory. Each extraction first cuts the branch's own root→leaf conversation into a throwaway file (`AgentManager.branchFile`), forks that file (`forkAndContinue`), composes the store's instruction from shared sections (stamped with the **trunk's day**, not wall-clock), hands the forked assistant one follow-up turn under a file-tool allowlist, then sweeps emptied files. A branch's stores run concurrently by default (`parallelizeExtraction`, on) because they write disjoint directories, and both forks are awaited to settlement before the shared branch file is deleted; branches stay serial, since same-store extraction across branches would read-modify-write the same canonical store files (a single synthesis writer is what would unlock cross-branch concurrency — see DLT-180). A single branch's failure is isolated — logged and skipped without a marker — and `extractBranches` throws after the loop if any branch failed, so the close does not advance to the maintenance phases and the trunk stays unclosed for a clean retry. The maintenance phases have no conversation to fork, so they run bare headless side-runs (`SideRunner.run`) over the store on disk, each guarded by an idempotent session-entry marker so a crash re-runs a phase at most once.

Branch cutting is non-destructive by construction: pi's `SessionManager.createBranchedSession` rewrites the manager it runs on *in place* (repointing its file and entry index to the branch), so `branchFile` runs it on a manager loaded fresh from disk — never the live trunk session. Running it on the live session would otherwise turn the trunk into branch 1, making every later branch unreachable (see `docs/reference/pi-sdk-notes.md`).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/memory/index.ts` | Wiring and config schema (`enabled`, `maintenance`) | `migrate-memory-stores` bootstrap registered before `init-memory-layout`; the whole extraction + maintenance pipeline is one `memory-trunk-close` processor; transcript-prune is the only cron |
| `src/extensions/memory/migration.ts` | `migrateMemoryStores` — one-time fold of legacy `facts/`+`preferences/` into `topics/` | State-based detection (legacy-content presence), no persisted marker; two commits bracket the removal; the host removes the legacy stores outright after the fold commits; hard error aborts without removing |
| `src/extensions/memory/layout.ts` | Store paths, the store-set constants (`MEMORY_STORES`/`INDEXED_STORES`/`EXTRACTION_STORES`, `ExtractionStore` type, `FORK_WRITE_STORES`), `ensureMemoryLayout` (dirs + index seeding), `listMarkdown`, `sweepEmptyMarkdown`, `isBlankMarkdown` | The constant arrays are the single source of truth every module iterates; episodic stays in `MEMORY_STORES` but is not indexed; learnings is in `MEMORY_STORES` + `INDEXED_STORES` but excluded from `EXTRACTION_STORES` (folded into the topics fork); `ExtractionStore` keys `STORE_INSTRUCTIONS` so the no-learnings-fork invariant is compile-time; `FORK_WRITE_STORES` declares which dirs each fork writes (drives the post-run sweep); `isBlankMarkdown` is the single "what counts as content" threshold shared by the sweep and the migration detection gate |
| `src/extensions/memory/indexes.ts` | `buildMemoryContext`, `formatMemoryIndex` | One loop over `INDEXED_STORES` (topics + learnings); `formatMemoryIndex` capitalizes the store name → `## Topics Index` / `## Learnings Index`; strict entry regex — malformed lines vanish |
| `src/extensions/memory/extraction.ts` | `storeInstruction(store, workspaceRoot, date?)` — the per-fork follow-up prompt; `TOPICS_BASE_PROMPT`; `LEARNINGS_EXTRACTION_SECTION`; `EPISODIC_BASE_PROMPT`; `SILENT_BACKGROUND_SECTION`; `Runner` type | The `topics` entry is a combined topics+learnings instruction that classifies each signal inline and writes to one store (the shared fork); `STORE_INSTRUCTIONS`/`storeInstruction` are keyed on `ExtractionStore`; `scopeSection`/`SILENT_BACKGROUND_SECTION` accept one or more stores so the topics fork names both directories; `date` (defaults to today) stamps the episodic `{date}.md` filename — the close pipeline passes the trunk's day so a late close still files under the day it happened |
| `src/extensions/memory/maintenance.ts` | `maintenanceSystemPrompt`, `contextMaintenanceSystemPrompt`, cross-store manifests, `runMaintenanceTick`, `runContextMaintenanceTick` | `TOPICS_MAINTENANCE_PROMPT` + `LEARNINGS_MAINTENANCE_PROMPT` (dedup, safety-net draft promotion, prune stale/contradicted/resolved, consolidate per-incident fragmentation, repair `## Drafts`/`## Confirmed` structure); `~50`-line guard; authority `Skills > Topics > Context` with learnings orthogonal; learnings gets a maintenance pass by membership and the weekday-light/Sunday-rebuild gate by being a non-episodic `MEMORY_STORES` member |
| `src/extensions/memory/close-pipeline.ts` | `createTrunkClosePipeline` — the phased, marker-guarded trunk-close pipeline (extract → prune → consolidate → core-context) | Pluggable seam: phase bodies reuse `maintenance.ts` logic behind stable exported functions; `extractBranches` iterates `EXTRACTION_STORES` (so learnings spawns no fork), runs a branch's forks concurrently and branches serially — `parallelizeExtraction` toggles it and all forks settle before the shared branch file is deleted — and sweeps each fork's `FORK_WRITE_STORES`; markers written only after a body completes; carries `statusLabel: "Processing memories"` (and `transcript-archive` carries "Archiving transcript") so the close surfaces friendly progress on the channel's preparation lead-in |
| `src/extensions/memory/prompts.ts` | Shared prompt sections: store purpose (with the orthogonal learnings authority bullet), context/skill dedup, workspace validation, index update, light index maintenance, scope | Topic-oriented sections; the facts-vs-preferences classification-examples section is removed; authority hierarchy `Skills > Topics > Context` with learnings stated as orthogonal; `scopeSection` accepts one or more stores |
| `src/extensions/memory/archive.ts` | `createTranscriptArchiveProcessor` (`finalize`) writes archives; `pruneTranscripts` (cron) deletes old ones | Names archive after the JSONL header session id; age-based prune by mtime; both never throw |
| `src/util/dates.ts` | `localIsoDate(date?)` (neutral util, used by memory extraction and the context processor) | Local timezone — date-stamped artifacts follow the user's day, not UTC; it is the *default* for `storeInstruction`, but the trunk-close pipeline passes the trunk's own day instead of wall-clock |
| `src/agent/file-tools.ts` | `FILE_EDIT_TOOLS` (neutral file-tool allowlist, shared by memory extraction, the context processor, and the maintenance/migration side-runs) | Hard tool limit for silent file-maintenance forks/runs |

### Cross-Layer Contracts

- **Store set as the seam.** `MEMORY_STORES`, `INDEXED_STORES`, and `EXTRACTION_STORES` in `layout.ts` are the single source of truth. Extraction iterates `EXTRACTION_STORES`; directory creation, the prune loop, maintenance, and cross-store manifests (`buildCrossStoreManifest`, `buildStoreManifestForContext`) iterate `MEMORY_STORES`; index seeding and context injection iterate `INDEXED_STORES`. Learnings propagates to dir/index/injection/maintenance/cross-store-visibility by membership, and is excluded from extraction by *non*-membership — the asymmetry that folds it into the topics fork. `ExtractionStore` (a type derived from `EXTRACTION_STORES`) keys `STORE_INSTRUCTIONS` and types `storeInstruction`/`branchStoreInstruction`, so the no-learnings-fork invariant is enforced at **compile time**, not only by the `extractBranches` loop constant.
- **Headless-LLM seam.** Extraction (via `forkAndContinue` on a forked branch) and maintenance/migration (via `Runner.run`) all use the `SideRunner` with the shared `FILE_EDIT_TOOLS` allowlist and the `processor` tier. The topics+learnings fork stays file-tool-limited even though it writes two directories; the migration reuses the same runner/tool/tier the maintenance phases use.
- **Commit seam.** All workspace-mutating passes commit through the shared `commitChanges` helper (`app.git.createCommitAgent("workspace")` + `commitAll`). The migration uses the same helper so its fold and removal land as git commits (which is also the backup/recovery mechanism).
- **Index contract.** Every agent that creates/modifies/deletes a topics or learnings file also maintains that store's `MEMORY.md` per the `INDEX_UPDATE_SECTION` rules (the shared topics+learnings fork keeps both indexes in sync on writes). `ensureMemoryLayout` is idempotent and only seeds an absent index.

## Key Decisions

### One unified topics store (not a reformed two-store model)

**Choice**: The topics store is a single topic-oriented store; each `<topic>.md` file holds everything known about a subject — both stable reference facts and subjective preferences — together in free-form prose. (Episodic and learnings are separate stores of different kinds — narrative and experience — not a split of this knowledge store.)
**Why**: The classification/routing boundary is the root problem — any model that keeps two homes for a subject reintroduces the guess of "which store" and a misclassification backstop to correct it. A single home per subject means recall never depends on the classification.
**Alternatives Considered**:
- Two stores (objective facts / subjective preferences) with a better classifier: the boundary itself is the defect; a better classifier can still be wrong and still fragments a subject across files.
- Two stores, one file per subject with typed `## Facts`/`## Preferences` sections: a prescribed structure re-introduces the boundary inside the file.
**Consequences**:
- Pro: one home per subject, simpler recall, less prompt surface; preferences gain a defined place in the authority hierarchy.
- Con: loses the (lossy) signal of "fact vs preference"; acceptable — the content is preserved and the distinction was the bug.

### Authority hierarchy: Skills > Topics > Context, with learnings orthogonal

**Choice**: Topics hold the durable detail (reference facts + preferences) and sit above the root context files (SOUL/USER/AGENTS); skills remain highest. Learnings does **not** join this ladder — it is orthogonal to it: a learning and a topic about the same subject are different *kinds* of information (experience vs knowledge), so neither overrides the other, and both are surfaced.
**Why**: Topics hold the durable detail (reference + preference); context files are concise summaries/pointers, so they defer to topics. Under the old `Skills > Facts > Context`, preferences had no tier — folding them into topics gives them a defined place. Learnings is kept off the ladder because placing it on it would imply a learning defers to or overrides a topic about the same subject, collapsing two kinds of information into one conflict-resolution axis.
**Alternatives Considered**: Topics below context files — context files are deliberately terse and would wrongly outrank the detailed topic content; learnings inserted into the ladder (e.g. `Skills > Topics > Learnings > Context`) — rejected for the same collapsing-the-two-kinds reason.
**Consequences**: When a topic duplicates a context file, the topic trims to a pointer; when content conflicts, the higher source wins. A learning is never folded into a topic nor pruned in deference to one; both coexist and surface independently about the same subject.

### Fork-continue each branch instead of replaying the transcript as text

**Choice**: Extraction forks each branch of the day's trunk (`forkAndContinue`, backed by `SessionManager.forkFrom`) and hands the same assistant one follow-up user instruction per store, hard-limited to `FILE_EDIT_TOOLS`. The source transcript is never mutated.
**Why**: The agent that just had the branch already holds it in full fidelity (real turns, tool activity, thinking), so "use what you already know" is more faithful than re-reading a flattened, truncated blob. `forkFrom` copies the history without touching the original; the `tools` allowlist keeps the fork from messaging the user or firing tasks.
**Alternatives Considered**:
- Transcript re-read + ephemeral headless run: bounded cost but lossy — flattened to text, tail-truncated, tool activity and thinking discarded.
- `complete()` one-shots without tools: the agent could not read existing memories, grep for overlap, or edit files.
**Consequences**:
- Pro: full-fidelity context; thin processors over `forkAndContinue`.
<<<<<<< HEAD
- Con: the fork loads the branch turns as live tokens, so cost scales with branch length (×2 stores).
- The branch set is the **topic-filtered** enumeration: summarized tangents and reversed summaries are excluded (a tangent's content is already in the main-line summary; a reversed summary is a decision the user undid) — the same `kind` discriminator that serves `ask_branch` and branch enumeration serves extraction, so only genuine topics are forked.
=======
- Con: the fork loads the branch turns as live tokens, so cost scales with branch length (×2 forks per branch: episodic + the shared topics+learnings fork).
>>>>>>> katachi/DLT-123

### Empty-file deletion protocol with host-side sweep

**Choice**: Agents have no delete capability; the scope prompt instructs them to overwrite obsolete files with empty content, and the host runs `sweepEmptyMarkdown` over the target store after every extraction and maintenance run.
**Why**: pi's tool set has no delete, and granting `bash` for `rm` would hand an unsandboxed shell to a prompt-driven agent. Emptying is expressible with `write` and is idempotent.
**Alternatives Considered**: A custom delete tool (widens blast radius); bash restricted by prompt (not enforcement).
**Consequences**: Worst-case agent damage within deletion semantics is an emptied file, recoverable from git; the sweep doubles as cleanup for stray empties.

### Static index injection instead of retrieval

**Choice**: A context section injects the memory layout description plus the parsed topics and learnings `MEMORY.md` indexes once per session; the agent greps/reads memory files on demand. No similarity search, no per-message retrieval call.
**Why**: The index is small and always relevant; pushing file discovery to the agent's own tools avoids a retrieval subsystem. Episodic files are date-organized and addressed by the layout description alone.
**Alternatives Considered**: Per-message LLM retrieval (latency + failure mode for marginal gain); inlining full contents (blows up the context window).
**Consequences**: Constant, predictable context overhead; index quality maintained by the same agents that write the files. Recall depends on the agent choosing to read files — a weak description hides a memory.

### Shared topics+learnings fork: one pass classifies and folds every signal

**Choice**: The `topics` extraction fork is a shared topics+learnings fork. A single pass folds both factual and preferential signals into topic files AND classifies each signal inline as topic content or learning content, writing each to exactly one store (`topics/` for stable knowledge/preferences, `learnings/` for recurring friction as a draft). There is no facts-vs-preferences classification self-check, no separate AGENTS.md-preference skip, and no separate learnings fork.
**Why**: With one home per subject there is no topics-internal routing decision; the only routing is topic-vs-learning, and making it a single inline decision by one agent — rather than two independent forks — guarantees the two stores never duplicate a signal (the agent that classifies is the agent that writes). A signal with both stable-knowledge and friction character is classified by its primary aspect and written to one store.
**Alternatives Considered**: A separate learnings extraction fork (the uniform "iterate every store" path) — rejected: two independent forks could write the same signal to both stores with no shared decision; two extraction passes writing to one topics store — with one topics store there is nothing to route between.
**Consequences**: Pro — one classification decision per signal; duplication-free by construction; per-branch fork cost stays at two (episodic + the combined fork). Con — the fork writes two directories, so its scope and silent-background sections name both and its post-run sweep covers both; mitigated by `scopeSection`/`SILENT_BACKGROUND_SECTION` accepting multiple stores and `FORK_WRITE_STORES` driving the sweep.

### Learnings folded via the store-set split, enforced at compile time

**Choice**: `learnings` is added to `MEMORY_STORES` (directory creation, maintenance iteration, cross-store manifests) and `INDEXED_STORES` (index seeding + context injection) but excluded from a new `EXTRACTION_STORES = ["episodic", "topics"]`; the topics fork then folds it. `ExtractionStore` (derived from `EXTRACTION_STORES`) keys `STORE_INSTRUCTIONS` and types `storeInstruction`/`branchStoreInstruction`.
**Why**: The uniform code paths iterate a store-set constant, so membership — not new wiring — propagates a store to layout, injection, maintenance, and cross-store visibility. The one asymmetry (in the maintenance/index/dir sets, absent from extraction) is the entire mechanism that folds learnings into the topics fork; keying `STORE_INSTRUCTIONS` on `ExtractionStore` makes "learnings has no extraction fork" a compile-time error if violated rather than a runtime crash on an undefined instruction.
**Alternatives Considered**: A boolean/flag per store gating extraction — a second source of truth that can drift from the loop constant; a runtime guard on `STORE_INSTRUCTIONS[store]` — leaves the invariant unenforced until a fork actually spawns.
**Consequences**: Pro — adding a future store is membership in the right set(s); the no-learnings-fork invariant is checked by `tsc`. Con — the store-set split is one more concept to hold; documented at the seam (`layout.ts`).

### Draft→permanent lifecycle: "recurring" is enforced by repetition, not at capture time

**Choice**: Every new friction is captured under `## Drafts` (tentative); it graduates to `## Confirmed` (permanent) only when a later extraction reads existing learnings and recognizes the same friction recurring (or a maintenance pass corroborates it as a safety-net). A friction a later session shows was resolved is corrected or removed, never promoted.
**Why**: Nothing valuable is lost on first sighting, but only repetition earns permanence — keeping the store focused on patterns that actually bite repeatedly. Recurrence is detected by the agent reading the (small) learnings store and matching, reusing the topic store's proven read-before-write-and-match idiom — no embedding/retrieval subsystem, consistent with the static-index-injection decision.
**Alternatives Considered**: Gate at capture time (only record friction already seen recurring) — loses high-value one-off lessons before they can recur; an embedding-based similarity match — a heavyweight index/model dependency for a small store.
**Consequences**: Pro — no lost lessons; the store self-curates toward confirmed patterns; no new subsystem. Con — the store carries drafts that may never recur (acceptable given the visible tentative marking) and match reliability depends on the agent re-reading drafts — mitigated by the explicit "read existing learnings before deciding" instruction and the maintenance safety-net.

### The learnings index surfaces all slug files, including draft-only ones

**Choice**: `learnings/MEMORY.md` lists every slug file (drafts and confirmed alike); the draft/confirmed state lives *inside* the file via the `## Drafts`/`## Confirmed` headers, not flagged in the index.
**Why**: Consistent with the idempotent index rules (the index mirrors actual files) and maximizes proactive awareness of emerging friction. The tentative-vs-permanent distinction is already visible inside the file, so the index does not duplicate it.
**Alternatives Considered**: Hide draft-only files until promoted — blinds the agent to emerging friction and forces the index to track per-file draft/confirmed state that drifts from "index mirrors files".
**Consequences**: Pro — simple, consistent index model; proactive awareness. Con — tentative content appears in every session's context; acceptable given the low per-entry cost and the explicit tentative marking inside files.

### Two-section draft representation (`## Drafts` / `## Confirmed`)

**Choice**: Each learnings slug file carries `## Drafts` (tentative) and `## Confirmed` (permanent) sections; drafts move to Confirmed on promotion (a within-file relocation, not a file rename).
**Why**: The tentative state is visible at a glance in plain markdown, needs no parser, and is consistent with the free-form prose convention of the sibling stores.
**Alternatives Considered**: A YAML front-matter status marker (`status: draft|confirmed`) — more machine-parseable, but adds a parser concern, is invisible to a human skimming the file, and diverges from the sibling-store convention.
**Consequences**: Pro — human-readable, no parser, consistent with sibling stores; maintenance can detect/repair a malformed structure in place. Con — a single file can hold both states, so promotion is a within-file move.

### Policy lives in composed prompt sections

**Choice**: Store policy (dedup, validation, consolidation, index upkeep, scope, the `Skills > Topics > Context` authority order) is encoded as module-level prompt constants in `prompts.ts`, composed per store in `STORE_INSTRUCTIONS` and per maintenance tick in `maintenanceSystemPrompt`.
**Why**: The same authority hierarchy and index rules apply across extraction and maintenance; shared constants keep behavior consistent and reviewable as text, with `$WORKSPACE`/`{date}` substitution as the only templating.
**Alternatives Considered**: Per-store monolithic prompts — invite policy drift.
**Consequences**: Changing a policy is a one-line diff applying everywhere; tests assert prompt composition cheaply. Behavior is enforced by instruction, not code — regressions surface as store quality drift.

### Trunk-close pipeline: one phased, marker-guarded processor

**Choice**: Extraction and maintenance run as a single `memory-trunk-close` `main`-phase post-processor whose body is a strictly-ordered pipeline — per-branch extraction → prune → consolidate → core-context — each step guarded by an idempotent marker written to the session only after its body completes (an instance of [DES-008](../design/DES-008-marker-computed-effective-state.md)). Maintenance no longer runs on separate nightly crons; it folds into the prune and core-context steps. The phase bodies are a pluggable seam: they reuse `maintenance.ts` logic behind stable exported functions, so a later consolidation change (DLT-173) replaces them without touching the trigger, ordering, or marker machinery.
**Why**: Tying extraction and maintenance to trunk close (rather than one post-processor per store at every session close plus independent nightly crons) batches the LLM-heavy memory work to once per day and makes the whole pass idempotent and crash-recoverable at phase granularity. The marker-after-body ordering means a crash mid-phase re-runs that phase cleanly on the next close or recovery.
**Alternatives Considered**: One post-processor per store at session close plus staggered nightly crons — more LLM runs per day, and maintenance decoupled from the session lifecycle.
**Consequences**: Pro: one place for all daily memory work; marker-guarded idempotency; a clean slot for DLT-173's richer consolidation. Con: memory work is deferred to trunk close, so a crash before close delays it to recovery; topics maintenance is no richer than before until DLT-173.

### Episodic dating follows the trunk's day, not wall-clock

**Choice**: The per-branch episodic instruction stamps `{date}` with the **trunk's own calendar day**, not `localIsoDate()` at extraction time. The close pipeline threads `trunk.day` into `storeInstruction`, and `recoverStaleTrunks` derives a recovered trunk's day from its session-header creation instant (never defaulting to `today`) so the day is authoritative even when a close runs late.
**Why**: A trunk closed late — a missed nightly close, recovery after downtime, multi-day catch-up — happened on its own day, not the day the close finally ran. Dating by wall-clock filed the previous day's memories under today's `episodic/YYYY-MM-DD.md`, mixing days; this surfaced when a 2026-06-17 trunk recovered on 2026-06-18 wrote into `2026-06-18.md`.
**Alternatives Considered**: Subtract one day ("previous day") — wrong for same-day nightly closes and for multi-day-stale trunks; deriving the date from message timestamps inside the fork — fragile and duplicates the day the trunk already knows.
**Consequences**: Episodic entries always land under the day they happened. The fix has two halves (extraction uses the passed day; recovery preserves the real day), so both the normal late-close path and the bare-`unclosed` recovery path are correct.

### One-time migration: state-based detection, no persisted marker

**Choice**: A `migrate-memory-stores` bootstrap hook (registered before `init-memory-layout`) folds legacy `facts/`+`preferences/` into `topics/`. Detection is state-based: it runs iff a legacy store holds any non-empty `.md`, and is a no-op once both are gone (and on fresh installs, which never create them). No DB key, file sentinel, or migration record is written. The merge is fully agent-decided (one side-run over all old files plus existing topics); the agent only writes under `topics/` and the host removes the legacy stores outright after the fold commits — a blank-only sweep would leave content-bearing files behind and re-trigger detection every startup. Two commits bracket the removal (fold → commit → remove → commit); a hard agent error aborts without removing so the pass retries next startup.
**Why**: The removed-legacy-stores state is intrinsically the completion signal (reached only by the final removal), so a separate marker is redundant — mirroring the legacy migration subsystem's convention of detecting work by state presence rather than persisted flags. Keying on legacy-content presence (not `topics/` presence) makes an interrupted fold re-run correctly: after a fold that created `topics/` but crashed before the removal, the legacy stores still hold content, so the check correctly re-runs. The fold-before-remove ordering and the fold's merge/dedup guarantee no data loss, and the committed stores are git-recoverable.
**Alternatives Considered**: `topics/` presence as the trigger (false "done" after a mid-fold crash); a DB `app_state` completion key (a second source of truth that can drift); a file sentinel (redundant, a hidden workspace artifact).
**Consequences**: Pro: no marker state to manage or drift; the filesystem is the single source of truth. Con: a re-run after an interrupted fold relies on the agent's merge/dedup (mitigated by merge instructions + the next maintenance pass). This state-based, marker-free migration-detection idiom recurs across the migration subsystem — see [DES-006](../design/DES-006-state-based-migration-detection.md).

### Deterministic age-based transcript pruning instead of an agent tick

**Choice**: Transcript retention is a plain `fs` routine (`pruneTranscripts` in `archive.ts`) on its own cron — it deletes `.jsonl` files whose mtime is strictly older than `transcriptRetentionDays`, never runs a headless agent, and `0` retention disables it (retain forever).
**Why**: Transcripts are raw JSONL archives, not prose that benefits from consolidation; age-based deletion needs no LLM. It warrants no new ADR/DES (reuses ADR-006 scheduler, ADR-007 config).
**Alternatives Considered**: A maintenance tick store (forces an LLM call onto pure deletion); folding the prune into the episodic step (couples deletion to an LLM run's success).
**Consequences**: Cheap, predictable, tolerant of I/O errors; deletion is unconditional by age.

## System Behavior

### Scenario: Trunk-close pipeline

**Given**: A trunk closes with branches
**When**: The `memory-trunk-close` processor runs
**Then**: Each unextracted branch is forked for episodic extraction and the shared topics+learnings fork (failures isolated), the prune step runs topics + learnings + episodic maintenance, the consolidate step is a marker-guarded no-op, and the core-context step reviews SOUL/USER/AGENTS — each step guarded by an idempotent marker and committing its edits; the transcript archive and git commit run later in `finalize`.

### Scenario: Topics agent merges two files

**Given**: `memories/topics/` contains two overlapping files
**When**: The topics extraction or maintenance agent consolidates them, writing the merged content into one and emptying the other
**Then**: After the run, `sweepEmptyMarkdown` deletes the emptied file and the prompt's index rules have the agent update `MEMORY.md` to match.

### Scenario: Extraction folds a fact and a preference about the same subject

**Given**: A closed branch where the user stated a stable fact and a related preference about the same subject
**When**: Extraction runs
**Then**: Both signals land in the same topic file (create-or-merge); no classification self-check runs and nothing is routed between stores; `MEMORY.md` is updated once.

### Scenario: Shared fork classifies a fact and a friction about the same subject

**Given**: A closed branch containing both a stable fact about a subject and a recurring friction
**When**: The shared topics+learnings fork runs
**Then**: The fact is written to `memories/topics/` and the friction to `memories/learnings/` by the same pass — not two passes; a dual-character signal lands in exactly one store, by its primary aspect (a fact-that-bites is recorded in `learnings/` as the lesson, not also restated as a topic).

### Scenario: First sighting of a friction becomes a draft

**Given**: A session surfaces a recurring-flavored friction not yet in `learnings/`
**When**: The shared fork folds the branch
**Then**: The friction is written under `## Drafts` in the matching broad-slug file (created if none fits) — tentative, not confirmed; a single occurrence does not yet establish a recurring pattern.

### Scenario: Recurring friction is promoted

**Given**: A `## Drafts` entry exists for friction X
**When**: A later session's shared fork reads existing learnings and recognizes the same friction X recurring
**Then**: The entry is moved from `## Drafts` to `## Confirmed`. (A draft an extraction fork missed is additionally promoted by the learnings maintenance pass as a safety-net.) Repetition is the signal this is a real learning.

### Scenario: A resolved friction is corrected, not promoted

**Given**: A draft describes a friction a later session shows was resolved (e.g. the flaky test was fixed)
**When**: Extraction or maintenance runs
**Then**: The contradiction is resolved — the draft is corrected or removed — it is not promoted. A resolved friction is the opposite of recurrence; promoting it would record a falsehood.

### Scenario: A one-time event is not a learning

**Given**: A branch contains a one-time event (a single bug fix, an outage)
**When**: Extraction runs
**Then**: It is not written to `learnings/` (it belongs in episodic); only friction/lessons/reflections with experience character become learnings. A branch with no learnings signal creates no learnings file and throws no error.

### Scenario: Learnings fragmentation is consolidated

**Given**: Per-incident learnings files fragmenting one friction theme (the anti-pattern the file conventions forbid)
**When**: The learnings maintenance pass runs
**Then**: They are consolidated into a single broad slug file; a file whose `## Drafts`/`## Confirmed` structure is malformed is repaired in place, or — if unrecoverable — left for the next pass rather than crashing.

### Scenario: Sunday index rebuild

**Given**: The topics or learnings maintenance step runs on a Sunday
**When**: `maintenanceSystemPrompt` is assembled
**Then**: The prompt carries the full `## Memory Index Rebuild (full)` section (describe every file, consider structural merges/renames, rewrite `MEMORY.md` from scratch) instead of the weekday consistency check; learnings descriptions are written by the file's overall theme regardless of the draft/confirmed mix.

### Scenario: Legacy workspace migration

**Given**: A workspace with legacy `facts/`/`preferences/` files and no `topics/`
**When**: Bootstrap runs
**Then**: The migration hook detects legacy content; one agent run folds every old file into `topics/` (same-subject files across both old stores merge into one; distinct subjects stay distinct), the fold is committed, the host removes the old stores and commits, and the first session's injected index shows the migrated topics.

### Scenario: Migration interrupted mid-fold

**Given**: The migration crashed after creating some `topics/` files but before removing the old stores
**When**: The system restarts and the hook runs
**Then**: The legacy stores still hold content, so the pass re-runs; the agent re-folds, merging into the existing topic files rather than duplicating; then the old stores are removed. No data is lost (pre-migration state is git-recoverable).

## Notes

- `transcript-archive` and `git-commit` share the `finalize` phase and run concurrently, so an archived transcript may land in the working tree after that session's commit — the git processor's second commit pass (it re-checks for uncommitted changes) or the next session's commit picks it up.
- Completed processors and pipeline steps are recorded per name on the session row and skipped on re-runs, which makes crash-recovered post-processing (dangling sessions closed at startup) resume where it left off.
- `localIsoDate` deliberately uses the local timezone so episodic filenames match the user's perception of "today".
- The cross-store manifest lists names and paths only — content stays out of maintenance prompts to keep them bounded.
- Git is the migration backup: because the stores are committed markdown, the pre-migration state is always the prior commit.
