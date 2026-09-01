# pi SDK — verified API surface (0.79.6)

Verified against the installed `@earendil-works/pi-coding-agent` package (docs and `.d.ts` shipped in the package). This is the ground truth for how Tachikoma embeds pi. Re-verify on every pi upgrade — the project is pre-1.0 and fast-moving. The surface was originally documented at `0.79.1`; the load-bearing daily-trunk primitives (`branch`/`branchWithSummary`/`appendCustomEntry`, the append-only re-seat model DLT-181 rollback depends on) were re-confirmed present and stable at `0.79.6` during DLT-181 implementation. Re-verify on the next minor bump.

## Packages

| Package | Role | Notes |
|---|---|---|
| `@earendil-works/pi-coding-agent` | Embeddable SDK + CLI: `createAgentSession`, sessions, extensions, skills, settings | Node >= 22.19. The package we build on. |
| `@earendil-works/pi-agent-core` | `Agent` class + agent loop | Accessed via `session.agent` |
| `@earendil-works/pi-ai` | Multi-provider LLM API: `complete`, `completeSimple`, `stream`, `streamSimple`, `getModel`, typebox helpers, validation | Used directly for classifier/extraction side-calls |
| `typebox@1.1.38` | Schema language (the unscoped 1.x package, NOT `@sinclair/typebox`) | Pinned to pi's exact version |

## Embedding model

```ts
const authStorage = AuthStorage.create();            // ~/.pi/agent/auth.json + env vars
const modelRegistry = ModelRegistry.create(authStorage);

const loader = new DefaultResourceLoader({
  cwd, agentDir,
  systemPromptOverride: () => "...",
  additionalExtensionPaths: ["/path/ext.ts"],
  extensionFactories: [(pi) => { /* inline extension */ }],
  additionalSkillPaths: ["/path/skills"],           // merged into discovered skills
  noSkills: true,                                   // drops defaults; added paths still load
  skillsOverride: (current) => ({ skills: [...], diagnostics: current.diagnostics }),
  agentsFilesOverride: (current) => ({ agentsFiles: [...] }),
  promptsOverride: (current) => ({ prompts: [...], diagnostics: current.diagnostics }),
  eventBus,                                          // createEventBus() — shared with host
  settingsManager,
});
await loader.reload();

const { session, extensionsResult, modelFallbackMessage } = await createAgentSession({
  cwd, agentDir,
  model: getModel("anthropic", "claude-opus-4-5"),
  thinkingLevel: "medium",            // off|minimal|low|medium|high|xhigh
  tools: ["read", "bash", "my_tool"], // built-ins: read,bash,edit,write,grep,find,ls (default: read,bash,edit,write)
  customTools: [defineTool({ ... })],
  excludeTools: [...],
  resourceLoader: loader,
  sessionManager: SessionManager.create(cwd) | .inMemory() | .continueRecent(cwd) | .open(path) | .forkFrom(srcPath, targetCwd, sessionDir),
  settingsManager: SettingsManager.create() | .inMemory({...}),  // applyOverrides(), flush(), drainErrors()
  authStorage, modelRegistry,
});
```

Key facts:

- **One long-lived `AgentSession` per conversation** — pi sessions live in-process, with no per-message client recreation or resume bookkeeping. The coordinator holds a session open and replaces it on boundary.
- **`AgentSessionRuntime`** (`createAgentSessionRuntime` + factory using `createAgentSessionServices`/`createAgentSessionFromServices`) owns session *replacement*: `newSession()`, `switchSession(path)`, `fork(entryId, { position: "before"|"at" })`, `importFromJsonl()`. After replacement: `runtime.session` changes, event subscriptions must be re-attached, extensions are rebound (`session_shutdown` → reload → `session_start`).
- `session.prompt(text, { images, streamingBehavior, source, preflightResult })` resolves when the full run finishes. `steer()` / `followUp()` queue during streaming.
- `session.agent.state` is mutable: `messages`, `tools`, `systemPrompt`, `model`, `thinkingLevel`. `session.agent.waitForIdle()`.
- Compaction is built in: `session.compact(instructions?)`, auto-compaction via settings, extension hooks to customize.

### Session events (`session.subscribe`)

`agent_start/end`, `turn_start/end`, `message_start/update/end` (with `assistantMessageEvent.type === "text_delta" | "thinking_delta"`), `tool_execution_start/update/end`, `queue_update`, `compaction_start/end`, `auto_retry_start/end`. This is what channels consume (maps to domain `AgentEvent`s).

## Extension system

Extensions: TS modules (loaded via jiti, no compile) exporting `default function (pi: ExtensionAPI)` — sync or async (async factories complete before startup continues). Loaded from `~/.pi/agent/extensions/`, `.pi/extensions/`, settings `packages` (npm:/git:) and `extensions` arrays, `additionalExtensionPaths`, and inline `extensionFactories`.

### Events (`pi.on(event, handler)`)

| Phase | Events | Powers |
|---|---|---|
| Startup | `project_trust`, `session_start` (reason: startup/reload/new/resume/fork), `resources_discover` (contribute skillPaths/promptPaths/themePaths) | bootstrap, skill sources |
| Input | `input` (raw text; return `{action: "continue"|"transform"|"handled"}`) | input interception |
| Agent | `before_agent_start` (inject `{message}`, replace `{systemPrompt}`, read `systemPromptOptions`), `agent_start`, `agent_end` ({messages}) | context providers, per-message post-processing |
| Turn | `turn_start`, `turn_end` ({message, toolResults}), `context` (filter/rewrite messages before each LLM call), `before_provider_request` (replace payload), `after_provider_response` (status/headers) | context pruning, payload control |
| Messages | `message_start/update/end` (`message_end` can replace the message) | rendering, accounting |
| Tools | `tool_call` (block via `{block, reason}`, mutate `event.input` in place), `tool_result` (middleware-chained patches), `tool_execution_start/update/end` | permission gates, result rewriting |
| Session | `session_before_switch/fork/compact/tree` (cancellable), `session_compact`, `session_tree`, `session_shutdown` (reason: quit/reload/new/resume/fork) | lifecycle extensions |
| Model | `model_select`, `thinking_level_select` | model-aware UI/state |
| Bash | `user_bash` (intercept `!` commands; `createLocalBashOperations()` to wrap) | remote/sandboxed exec |

### ExtensionAPI methods

`registerTool` (works at load time AND at runtime — immediate availability, no reload; `promptSnippet` + `promptGuidelines` feed the system prompt; `prepareArguments` for legacy-shape migration; `terminate: true` result skips the follow-up LLM call — structured-output pattern), `registerCommand` (slash commands w/ completions), `registerShortcut`, `registerFlag`/`getFlag`, `sendMessage(msg, { deliverAs: "steer"|"followUp"|"nextTurn", triggerTurn })`, `sendUserMessage(content, { deliverAs })`, `appendEntry(customType, data)` (session-persisted state, not in LLM context), `setSessionName`/`getSessionName`, `setLabel`, `getCommands`, `registerMessageRenderer`, `exec(cmd, args, { signal, timeout })`, `getActiveTools`/`getAllTools`/`setActiveTools`, `setModel`, `getThinkingLevel`/`setThinkingLevel`, `events` (shared bus), `registerProvider`/`unregisterProvider`.

### ExtensionContext (`ctx` in handlers)

`ui` (notify/confirm/select/input/editor/setStatus/setWidget — TUI/RPC-bound; guard with `ctx.hasUI`, `ctx.mode`: `"tui"|"rpc"|"json"|"print"`), `cwd`, `sessionManager` (read access: `getEntries/getBranch/getLeafId/getLabel...`), `modelRegistry`, `model`, `signal` (agent abort signal), `isIdle()`, `abort()`, `hasPendingMessages()`, `shutdown()`, `getContextUsage()`, `compact()`, `getSystemPrompt()`, `isProjectTrusted()`.

`ExtensionCommandContext` (commands only) adds: `waitForIdle()`, `newSession({ parentSession, setup(sm), withSession(ctx) })`, `fork(entryId, { position, withSession })`, `navigateTree(targetId, { summarize, customInstructions, label })`, `switchSession(path, { withSession })`, `reload()`, `getSystemPromptOptions()`.

**Footgun:** after session replacement, captured `pi`/`ctx`/`sessionManager` objects from the old session are stale and throw. Only use the fresh `ctx` passed to `withSession`; capture only plain data across the boundary.

## Sessions on disk

JSONL trees at `~/.pi/agent/sessions/--<cwd-slug>--/<timestamp>_<uuid>.jsonl`; entries have `id`/`parentId`; entry types include `session` (header, may carry `parentSession`), `message`, `model_change`, `compaction`, `branch_summary`, `label`, `custom` (extension state via `appendEntry`), `custom_message`.

`SessionManager` instance API: `getEntries/getTree/getPath/getLeafEntry/getEntry/getChildren/getLabel/appendLabelChange/appendMessage/branch(entryId)/branchWithSummary(id, summary)/createBranchedSession(leafId)/getSessionFile`. Static: `list(cwd)`, `listAll(cwd)`, `forkFrom(srcPath, targetCwd, sessionDir?)` — reads the source read-only and writes a fresh history-seeded session file (`parentSession` points at the source).

**Post-processing patterns (two shapes):**
- **Conversation-aware (fork-continue):** for work that benefits from the live conversation — memory extraction, core-context — fork the just-ended session with `SessionManager.forkFrom` (wrapped by `AgentManager.forkAndContinue`) and send a follow-up user instruction. The same assistant continues with the full history live and persona intact; pass a `tools` allowlist to restrict it (the `tools` option is independent of the system prompt, so persona survives). The source transcript is never mutated.
- **Context-free (one-shot):** for work that needs no conversation context — boundary classification, rolling summaries, commit messages, nightly store maintenance — run `pi-ai` `complete()`/`completeSimple()` or a bare in-memory `createAgentSession` with a tool allowlist. `terminate: true` tool results give structured output.

## Skills

pi implements the **Agent Skills standard** (agentskills.io) — `SKILL.md` + YAML frontmatter (`name`, `description`, optional `license`, `compatibility`, `metadata`, `allowed-tools`, `disable-model-invocation`), the same format Tachikoma's workspace skills use. Discovery: `~/.pi/agent/skills/`, `~/.agents/skills/`, `.pi/skills/`, `.agents/skills/` (ancestors, post-trust), settings `skills` array, packages, `resources_discover` event (`skillPaths`). Progressive disclosure: descriptions in system prompt, agent `read`s SKILL.md on demand; `/skill:name` commands force-load. Missing description ⇒ skill not loaded; name collisions keep first found. Loader options (verified against 0.84.x `dist/`, guarded by the real-loader test in `tests/agent/force-load-skills.test.ts` — re-check on upgrade): `additionalSkillPaths` merges the given directories into discovered skills **even under `noSkills: true`** (a nonexistent added path is a diagnostic, not a throw), and `noSkills` plus non-empty added paths loads with `includeDefaults: false` — so an isolated run's catalog is exactly the added directories' skills. The skills-catalog *section* renders in the system prompt only when the built-in `read` tool is active (pi gates the catalog behind read because progressive disclosure loads bodies through it) — a run with no `read` (e.g. skill-evolution's proposal agent) still has the skills in `systemPromptOptions.skills` for extension code to read, and force-loads their content through a `before_agent_start` hidden message instead.

**Consequences for Tachikoma:** progressive disclosure handles base detection; a per-turn conversation-aware classifier (`before_agent_start` in the skills extension) augments it by recommending the skills most relevant to the latest message for the agent to load (it does not replace pi's discovery/loading); skill refresh is `ctx.reload()` rather than a filesystem watcher; skill `dependencies` and bundled agent definitions are not part of the standard — bundled agents can be supported via a subagent-style extension (see `examples/extensions/subagent/`, which discovers `agents/*.md` with frontmatter and spawns isolated `pi` processes).

## What pi does NOT provide (stays ours)

- **MCP**: none by design — agent tools are `pi.registerTool()` / `customTools` registrations.
- **Scheduling/cron, idle gating, buffered delivery** — host concern (croner + our buffer; inject via `pi.sendMessage`/`sendUserMessage` with `deliverAs` + `triggerTurn`).
- **Channels** (Telegram/REPL) — host consumes `session.subscribe()`; extension `ctx.ui` calls only function in pi's own TUI/RPC modes, so host-facing UX flows through our channel layer instead.
- **Cross-session orchestration** — boundary detection, session registry/recovery, topic resume: ours, on top of `SessionManager`/`AgentSessionRuntime`.
- **Database** — ours (drizzle + node:sqlite).

## Daily-trunk session-tree seam

The daily-trunk model (ADR-014) relies on these `SessionManager` primitives — re-verify each on a pi upgrade. They are wrapped in `src/agent/session-tree.ts` (tree ops) and `AgentManager.shadowFork` (classification fork):

| Primitive | Signature | Used for |
|---|---|---|
| `branchWithSummary` | `(branchFromId: string \| null, summary: string, details?: unknown, fromHook?: boolean) => string` | Collapse the current branch into a `branch_summary` entry at the base; returns the new summary id (advanced base). Does NOT record the abandoned leaf — we store `originalLeafId` in `details`. |
| `createBranchedSession` | `(leafId: string) => string \| undefined` | Write a new session file with only the root→leaf path; returns the new file path. Backs the shadow-fork classifier and per-branch extraction. **DESTRUCTIVE IN PLACE:** it repoints the manager it runs on at the new branch file and rebuilds that manager's entry index from only the branch path — so it MUST run on a `SessionManager` loaded fresh from disk, NEVER on a live session's manager. Run on a live session it silently turns that session into the branch: other branches become unreachable (the next call throws `Entry <id> not found`) and later appends — including idempotency markers — land in the wrong file. Always go through `AgentManager.branchFile`/`shadowFork`, which load a detached manager. |
| `getBranch` | `(fromId?: string) => SessionEntry[]` | Walk an entry to root in path order — branch enumeration / extraction slicing. |
| `appendCustomEntry` | `(customType: string, data?: unknown) => string` | Persist trunk/branch/boomerang state + idempotency markers out of LLM context. |
| `appendCustomMessageEntry` | `(customType, content, display: boolean, details?) => string` | Inject the related-branch pointer into LLM context with `display: false`. |
| `getEntries` / `getEntry` / `getLeafId` / `branch(id)` | as in the instance API above | Rebuild branch records on reload; re-seat the leaf onto the current base after `open` (pi sets the leaf to the LAST file entry, not the active tip). |
| `getHeader` | `() => SessionHeader \| null` | The session's creation instant (`header.timestamp`), wrapped by `sessionCreatedAt`. Recovers a stale trunk's true calendar day on a late close (`localDay(header.timestamp)`) instead of defaulting to the recovery day. |

`AgentManager.shadowFork(sourceFile, { systemPrompt, tier })` reuses the existing `open({ sessionFile, bare: true, tools: [] })` path rather than pi's lower-level `createAgentSessionServices`/`createAgentSessionFromServices` two-call construction — `bare` + empty `tools` already yields an extension-free, tool-free headless session, and `open` composes the loader/model/tier. The fork file is deleted on `dispose`. The source file is opened in a separate `SessionManager` and is never mutated (R6).

`AgentManager.branchFile(sourceFile, leafId)` is the non-fork sibling: it just cuts the root→leaf branch into a new file (for per-branch memory extraction and `ask_branch`) via the same detached-manager rule, returning the path (the caller opens/forks it separately). Both funnel `createBranchedSession` through `loadDetachedSession`, which is the single place that enforces "never branch a live session's manager."

## Misc verified utilities

`parseFrontmatter`, `withFileMutationQueue(path, fn)` (parallel tool calls mutating the same file MUST use it), `truncateHead/truncateTail/DEFAULT_MAX_BYTES (50KB)/DEFAULT_MAX_LINES (2000)`, `StringEnum` from pi-ai (use instead of `Type.Union` of literals — Google API compat), `isToolCallEventType`, `createCodingTools/createReadOnlyTools/create{Read,Bash,Edit,Write,Grep,Find,Ls}Tool` with pluggable `operations` (remote/sandbox exec) and bash `spawnHook`, `validateToolCall`, run modes (`InteractiveMode`, `runPrintMode`, `runRpcMode`).

API keys: runtime override (`authStorage.setRuntimeApiKey`) → `auth.json` → env (`ANTHROPIC_API_KEY`) → models.json fallback. OAuth (Claude Pro/Max) supported via `/login`-style flows.
