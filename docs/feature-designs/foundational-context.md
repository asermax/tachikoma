# Design: Foundational Context

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/foundational-context.md](../feature-specs/foundational-context.md)
**Status**: Current

## Purpose

Explains how the assistant's identity files (SOUL.md, USER.md, AGENTS.md) reach the pi system prompt, how the post-session updater that evolves them is structured, and how the harness's features are documented to the agent across the composed context (the two-tier inline/reference convention and its placement matrix, below).

## Problem Context

pi ships a coding-agent system prompt; Tachikoma needs a personal-assistant identity sourced from user-editable workspace files. Separately, those files must evolve from conversations without becoming noisy — one-off remarks must not rewrite the assistant's personality, but recurring patterns should.

**Constraints:**
- SOUL/USER reach the prompt through `provideContext` (system-prompt mode), whose `provide` may be async — but a simple synchronous `readFileSync` suffices here
- pi already discovers AGENTS.md from the workspace root; duplicating it would inject it twice
- The fork is tool-limited by a plain allowlist and there is no MCP — any agent-managed state must be a file (see `docs/reference/pi-sdk-notes.md`)

**Interactions:**
- The core base prompt (identity + guidance + workspace root) is installed as the `systemPromptOverride` per session open by `AgentManager` (see [agent-integration](./agent-integration.md)); SOUL/USER are appended on top via `provideContext`
- The `core-context` processor runs in the post-processing pipeline owned by the coordinator (see [conversation-loop](./conversation-loop.md)) and is registered by the context extension's own setup
- The periodic cleanup of these same three files (`runContextMaintenanceTick`, `memory-context-maintenance` cron) lives in the memory extension's maintenance module, not here — it complements the per-session updater by consolidating and pruning across the whole file rather than reacting to one conversation (see [memory](./memory.md))
- Workspace internals (`dataDir`) come from the core shell (see [core-shell](./core-shell.md))
- Context file changes are committed by the finalize-phase git processor (see [git-workspace](./git-workspace.md))

## Design Overview

Two halves with different lifetimes. The extension (`src/extensions/context/index.ts`) is process-scoped: a bootstrap hook creates SOUL.md and USER.md from templates on first run and caches their contents as a fallback, and two `provideContext` factories (SOUL, then USER) re-read both files from disk (`readFileSync`, falling back to the cached snapshot only on a read error) and append them to the system prompt on top of the core base prompt. A third factory injects the current date/time (configured timezone) as a hidden, persisted message refreshed at most once per 30 minutes via `provideDebouncedContext` (see [agent-integration](./agent-integration.md)) — the sole source of time-of-day awareness, since the core base prompt's `Current date:` header is date-only (baked per session) and pi's per-turn footer is date-only in the process timezone. The core base prompt itself (identity + shared `OPERATIONAL_GUIDANCE` plus interactive guidance + workspace root + a configured-zone `Current date:` header) lives in `buildMainSystemPrompt` ([DES-005](../design/DES-005-base-prompt-ownership.md)) and is installed by `AgentManager` (which threads `config.scheduler.timezone` into the header), not the extension. The updater (`src/extensions/context/processor.ts`) is a `preFinalize` post-processor: it expires and snapshots the pending-signals file, builds a follow-up instruction, then forks the just-ended pi session (`forkAndContinue`) so the same assistant — full conversation live in its history — edits the three context files and the signals file directly, hard-limited to file tools.

## System-Prompt Feature Documentation (Two-Tier)

The composed context documents the harness to the agent in two tiers ([DES-014](../design/DES-014-two-tier-agent-facing-documentation.md); landed in issue-445). **Inline sections** — the core base prompt plus each extension's `usage.ts` constant, delivered once per session as hidden messages via `provideContext` — carry only what the feature is, when to reach for it, and critical safety rules. Everything else (lists, edge cases, config knobs, mechanics the agent needs only occasionally) lives in **reference files** next to the owning module (`src/extensions/<name>/references/<topic>.md`, `src/agent/references/<topic>.md`), pointed to by a single canonical line from `referencePointer` (`src/agent/prompt-references.ts`) that the agent reads on demand — the same progressive-disclosure pattern skills use. Reference files ship with the package via `scripts/copy-assets.mjs` (tsc only emits JS).

Two structural rules govern placement. First, **ownership**: the core base prompt (`buildMainSystemPrompt` + `src/agent/references/`) documents only the conversation substrate the coordinator/channel core owns (steering, command routing, system-origin turns, delivery timing); extension-specific behavior is documented exclusively by its owning extension's section or reference — the core prompt never names an extension's tools or turn formats. Second, **scope matching**: a section describes only what its `sessionScopes` can call (main-only features are never described to background sessions, and vice versa).

The drift guards live in `tests/agent/prompt-references.test.ts`: a size budget over the complete static inline set, bidirectional pointer/reference integrity (every static usage constant either contains guidance or a resolvable pointer; every reference file is pointed to), and the extension-ownership deny-list on the core prompt.

### Placement Matrix

Each row records the coverage-pass decision (issue-445) for one agent-facing surface — annotations like "NEW" or "trimmed" describe that pass relative to the prior state, not a standing property. "Inline" sections are main-session delivered unless scoped otherwise; every inline section also carries its reference pointer.

| Surface | Tier | Scope | Rationale |
|---------|------|-------|-----------|
| Working hygiene (`OPERATIONAL_GUIDANCE`) | Inline (core) | all contexts | Frames every turn; already minimal; untouched by this pass |
| Conversation substrate: mid-exchange steering, `/queue`, command routing, bare-command pending prompts, system-origin turns, delivery timing | Inline (core, compact) + `src/agent/references/conversation.md` | main | Substrate the agent experiences every exchange — core-owned, so it belongs in the core prompt; detail (tier table, wrapper shape) demoted to the reference |
| Configuration surface | `src/agent/references/config.md` only (core) | main | Rarely needed; the agent can point the user at the file and read it itself — no standing inline cost |
| boundary — branch model, `/new` `/checkpoint` `/back` `/rollback`, `ask_branch`, related-branch injection, checkpoints | Inline (`## Branches`, NEW) + `references/branches.md` | main | Biggest prior gap: the agent saw branch summaries and the tool with no framing. Mechanics detail (boomerang, forced references, kill-switches) demoted |
| memory — `memories/` layout, read-on-demand, no direct writes | Inline (trimmed) + `references/memory.md` | main + background | What/when/critical-rule stays; per-store annotations and maintenance detail demoted |
| projects — submodule management, close-time persistence | Inline (hoisted + trimmed) + `references/projects.md` | main + background | Hoisted from a private constant so the sweep/budget enumerate it; auth detail demoted |
| git — auto-commit, `commit_workspace`, dedicated-tools rule | Inline (trimmed) + `references/git.md` | main + background | The dedicated-tools and no-manual-commit rules are critical and stay; the bash deny list demoted |
| skills — injected-skill authority, catalog habit, delegation | Inline (adjusted) + `references/skills.md` | main + background | Core's former `delegate_to_agent` bullet absorbed here (ownership rule); catalog/delegation mechanics demoted |
| skill-evolution — nightly self-improvement, pattern pages, proposal branches | Inline (`## Skill Evolution`, NEW) + `references/skill-evolution.md` | main | Was implemented but never loaded — wired into `firstPartyExtensions` by this change; the agent must know proposals are reviewable branches, not silent edits |
| workflows — step-directory processes | Inline (kept short) + `references/workflows.md` | main | What/when fits in three sentences; state-machine detail demoted |
| tasks — session vs background, scheduling, goal authoring | Inline (trimmed) + `references/tasks.md` | main + background | Goal authoring stays inline per R13 (goal meaning documented on every agent-facing surface) even though `BACKGROUND_GUIDANCE` restates it for the run itself; run limits/timeouts/retention and the `📋 Scheduled task:` turn shape demoted |
| detached-processes — long-running commands, no bash backgrounding | Inline (trimmed) + `references/processes.md` | main + background | The no-bash-backgrounding rule and check-before-judging stay; stderr detail demoted |
| notifications — background updates arriving as digests | Inline (`## Notifications`, NEW, small) + `references/notifications.md` | main | Main sessions only see the receiving side (digest as a system turn); `notify_user` is background-only and documented there |
| self-update — dedicated tools only, deferred re-exec | Inline (kept) + `references/updates.md` | main | The never-npm-directly rule is critical and short; rollback/check cadence demoted |
| telegram — file delivery, reactions, pins, inline buttons | Short inline section + `references/telegram.md` | main (registered only when the channel is configured) | Channel-dependent affordances; tools already carry `promptGuidelines`, so only the affordance overview needs saying |
| external — third-party extension install/update/list/uninstall | Inline (`## Extensions`, NEW, tiny) + `references/extensions.md` | main | The agent must know it can manage extensions and that installs apply on restart |
| bash-description — required `description` on bash | Omitted | — | Self-evident from the tool's parameter schema; no behavioral guidance adds anything |
| commands — channel-agnostic command pass-through | Omitted | — | No agent-facing surface; all command handling happens before/around the agent |
| context — SOUL/USER/AGENTS.md identity files | Omitted | — | The files *are* the prompt; the agent needs no guidance to be framed by them |
| Dynamic providers (memory indexes, projects snapshot) | Matrix-only (not static) | main + background | Content depends on session/workspace state — covered by this matrix, not the static sweep |

**Size budget**: the complete static inline set (core base prompt + every static usage constant) measures ≤ 10,500 characters (~8,930 before this pass); the budget binds — if coverage pushes past it, inline sections slim further and this matrix records the cut.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/context/index.ts` | Extension wiring: registers its own `core-context` post-processor, `load-context-files` bootstrap hook, `readOrCreate` with templates, two `provideContext` factories (SOUL, USER) that re-read from disk per session (`fresh()` with cached fallback) and append to the system prompt, and a `provideDebouncedContext` factory that injects the current date/time (configured tz, `formatTimestamp`) as a hidden message refreshed ≤ once / 30 min | Owns the `core-context` registration directly (no longer in memory); only ENOENT triggers template creation — other read errors abort startup; per-session `readFileSync` so processor edits apply next session; AGENTS.md deliberately untouched (pi native discovery); the core base prompt lives in core and is installed by `AgentManager`, not here ([DES-005](../design/DES-005-base-prompt-ownership.md)); current-time uses the debounced hidden-message mode (not the system-prompt append) because time is a refreshed situational fact, not standing identity |
| `src/extensions/context/processor.ts` | `createCoreContextProcessor` (`core-context`, `preFinalize`); pending-signals parse/serialize/clean; the full update policy as `INSTRUCTION_TEMPLATE` (follow-up user-instruction shape, with a silent-background directive) | Forks via `forkAndContinue` with the neutral `FILE_EDIT_TOOLS` allowlist (`src/agent/file-tools.ts`); uses the neutral `localIsoDate` util (`src/util/dates.ts`) — no longer imports from the memory extension; signals live in `dataDir`, outside user-visible workspace content |

## Key Decisions

### Core base prompt replaces pi's base; SOUL/USER append on top

**Choice**: The core (`AgentManager`) installs `buildMainSystemPrompt` (identity + guidance + workspace root) as pi's `systemPromptOverride`, replacing pi's coding prompt. SOUL.md/USER.md are then appended to the system prompt through `provideContext` (system-prompt mode), not baked into the override and not injected as separate messages.
**Why**: The standing identity must frame every turn by replacing pi's coding prompt (the only way to stop being a coding agent), so it is owned and installed by the core — independent of any extension. SOUL/USER are user-editable workspace content that *supplements* that identity; appending them via `provideContext` keeps the base prompt core-owned while letting the persona live in editable files, chained onto the override (`event.systemPrompt`) on every `before_agent_start`.
**Alternatives Considered**:
- Baking SOUL/USER into the core `buildMainSystemPrompt`: couples core to user-editable workspace files and reintroduces an extension→core file dependency
- Injecting SOUL/USER as hidden messages (the `customType` mode): they would not frame the turn as system-prompt material and would sit after the conversation rather than in the prompt
- Writing into AGENTS.md: conflates personality and user knowledge with operational instructions, and loses the template/bootstrap story

**Consequences**:
- Pro: the base identity is core-owned and uniform across every non-bare session, independent of which extensions are enabled
- Pro: AGENTS.md keeps its native pi semantics (re-read on every session open) with zero code
- Pro: SOUL/USER are re-read from disk per session, so edits (including the processor's own) apply on the next session without a restart; the bootstrap snapshot is only the read-failure fallback
- Con: SOUL/USER reach the system prompt as an append after the override rather than interleaved with the identity/guidance — acceptable, as ordering within the prompt does not matter for these sections

### Current time as a debounced hidden message, not a system-prompt append

**Choice**: Inject the current date/time (`formatTimestamp`, configured tz) as a hidden, persisted message refreshed at most once per 30 minutes via `provideDebouncedContext` (`customType: "current-time"`), rather than appending it to the system prompt every turn like SOUL/USER.
**Why**: Time is a situational fact that changes continuously — it needs neither to frame every turn (SOUL/USER do that) nor to be re-injected on every turn (which would churn the transcript). Neither the core base prompt's `Current date:` header (date-only, baked per session) nor pi's per-turn footer (date-only, process timezone) provides the time or the configured zone, so the agent otherwise has no time-of-day awareness. A leading-edge refresh-rate debounce (default 30 min — see [agent-integration](./agent-integration.md)) bounds staleness and the number of persisted entries while keeping the time current enough for conversational reference.
**Alternatives Considered**:
- Appending the time to the system prompt every turn (like SOUL/USER): always exact, but re-fires on every turn and time is more transient than the identity material that mode is for
- Baking the time into the `buildMainSystemPrompt` header: stale for the whole daily trunk (the header is deliberately date-only for this reason)
**Consequences**:
- Pro: the agent gains configured-zone time-of-day awareness it otherwise lacks; the debounce keeps the transcript clean
- Con: up to ~30-minute staleness, and one small persisted entry accumulates per window (accepted trade-offs for the chosen cadence)

### File-based pending signals edited by the agent

**Choice**: Store ambiguous signals as a dated markdown list at `{dataDir}/pending-signals.md`; the agent stages/promotes/discards by editing the file directly with its file tools, while the host only parses it for the prompt snapshot and enforces the 30-day expiry.
**Why**: The fork runs under a plain file-tool allowlist with no custom-tool transport, so the legacy implementation's dedicated MCP tools (`add_pending_signal`/`remove_pending_signal`) have nowhere to live — and they are unnecessary: a strict line format (`- **YYYY-MM-DD**: text`) lets the agent stage/promote/discard with the same `edit`/`write` tools it already uses for the context files. This is what unblocked the fork conversion: the pending-signals mechanism was already file-based, so no MCP porting was needed.
**Alternatives Considered**:
- Legacy MCP `add_pending_signal`/`remove_pending_signal` tools on the fork: pi has no MCP layer, and file edits cover the same need
- Database-backed signals: loses direct user inspectability and requires injection plumbing both ways

**Consequences**:
- Pro: one mechanism (file edits) for both context files and signal management; trivially inspectable
- Pro: host-side expiry is a pure function over the file, tested in isolation (`cleanPendingSignals`)
- Con: no all-or-nothing batch removal semantics — a sloppy edit can corrupt entries (unparseable content is detected and logged, not repaired)

### preFinalize phase, registered by the context extension

**Choice**: The processor runs in the `preFinalize` phase and is registered in the context extension's own `setup` (`src/extensions/context/index.ts`).
**Why**: Phase ordering guarantees context updates land after memory extraction (`main`) and before the git commit (`finalize`), so every closed session commits consistent context files — and because the pipeline orders by phase, that ordering holds no matter which extension registers each processor. Owning the registration in context keeps the feature self-contained and removes the former circular import (memory importing the context processor, the context processor importing back into memory for `localIsoDate`/`MEMORY_FILE_TOOLS`). The two helpers it needed moved to neutral homes — `localIsoDate` to `src/util/dates.ts` and the file-tool allowlist (renamed `FILE_EDIT_TOOLS`) to `src/agent/file-tools.ts` — so neither extension imports from the other.
**Alternatives Considered**:
- `main` phase: would race with extraction processors that read AGENTS.md for deduplication
- Leaving the registration in `memory/index.ts`: kept a cross-extension import in both directions and tied core-context updates to the memory extension's enabled state

**Consequences**:
- Pro: deterministic ordering relative to extraction and the workspace commit, preserved purely by phase
- Pro: the context feature is self-contained; no import cycle between the memory and context extensions; core-context updates no longer depend on the memory extension being enabled

## System Behavior

### Scenario: Ambiguous feedback recurs across sessions

**Given**: A staged signal "User seemed to prefer shorter responses" from a previous session
**When**: A new session closes where the user says "your answers are way too long"
**Then**: The forked agent sees the signal as `S1` in its follow-up instruction, updates SOUL.md with the conciseness preference, and deletes the promoted line from the signals file.

### Scenario: Session closes with no transcript

**Given**: A `PostProcessorContext` whose `transcriptPath` is null (the coordinator fills it from the session record's `piSessionFile`, so this is a session that produced no transcript)
**When**: Post-processing reaches `core-context`
**Then**: The processor's `process({ transcriptPath, log })` sees `transcriptPath == null`, logs a debug line, and returns without forking.

### Scenario: All staged signals have expired

**Given**: A signals file whose entries are all older than 30 days (or a file left with only the header after every signal was promoted)
**When**: The processor runs
**Then**: `cleanPendingSignals` deletes the file — a header-only file is treated as a normal empty end-state, not an anomaly worth warning about — and the prompt's signals section reads "No pending signals at this time."

## Notes

- Date comparison for expiry is lexicographic over zero-padded `YYYY-MM-DD` strings, using the configured-timezone date (`localIsoDate(date, timezone)` — the processor receives `config.scheduler.timezone`).
- The prompt instructs the agent to verify workspace claims (paths, configuration values) by reading/grepping before writing them, and to omit unverifiable claims — validation by instruction, not by host code.
- `SOUL_TEMPLATE` and `USER_TEMPLATE` seed a brand-new workspace; AGENTS.md has no template because pi treats a missing AGENTS.md as simply absent.
