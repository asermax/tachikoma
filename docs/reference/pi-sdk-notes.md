# pi SDK — verified API surface (0.79.1)

Verified against the installed `@earendil-works/pi-coding-agent@0.79.1` package (docs and `.d.ts` shipped in the package). This is the ground truth for how Tachikoma embeds pi. Re-verify on every pi upgrade — the project is pre-1.0 and fast-moving.

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
  sessionManager: SessionManager.create(cwd) | .inMemory() | .continueRecent(cwd) | .open(path),
  settingsManager: SettingsManager.create() | .inMemory({...}),  // applyOverrides(), flush(), drainErrors()
  authStorage, modelRegistry,
});
```

Key facts:

- **One long-lived `AgentSession` per conversation** — unlike the Claude Agent SDK (per-message client + resume), pi sessions live in-process. The coordinator holds a session open and replaces it on boundary.
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

`SessionManager` instance API: `getEntries/getTree/getPath/getLeafEntry/getEntry/getChildren/getLabel/appendLabelChange/appendMessage/branch(entryId)/branchWithSummary(id, summary)/createBranchedSession(leafId)/getSessionFile`. Static: `list(cwd)`, `listAll(cwd)`.

**Post-processing pattern (replaces Claude SDK session-fork):** open the transcript read-only via `SessionManager.open(path)`, walk entries, and run one-shot extraction with `pi-ai` `complete()`/`completeSimple()` or an in-memory `createAgentSession` seeded via `session.agent.state.messages`. `terminate: true` tool results give structured output.

## Skills

pi implements the **Agent Skills standard** (agentskills.io) — same `SKILL.md` + YAML frontmatter (`name`, `description`, optional `license`, `compatibility`, `metadata`, `allowed-tools`, `disable-model-invocation`) Tachikoma already uses. Discovery: `~/.pi/agent/skills/`, `~/.agents/skills/`, `.pi/skills/`, `.agents/skills/` (ancestors, post-trust), settings `skills` array, packages, `resources_discover` event (`skillPaths`). Progressive disclosure: descriptions in system prompt, agent `read`s SKILL.md on demand; `/skill:name` commands force-load. Missing description ⇒ skill not loaded; name collisions keep first found.

**Consequences for Tachikoma:** no LLM-based skill classifier needed (pi's progressive disclosure replaces it); the skills watcher/hot-reload is replaced by `ctx.reload()`; Tachikoma skill `dependencies` and bundled agent definitions are not part of the standard — agents can be ported via a subagent-style extension (see `examples/extensions/subagent/`, which discovers `agents/*.md` with frontmatter and spawns isolated `pi` processes).

## What pi does NOT provide (stays ours)

- **MCP**: none by design. In-process MCP servers → `pi.registerTool()` / `customTools`.
- **Scheduling/cron, idle gating, buffered delivery** — host concern (croner + our buffer; inject via `pi.sendMessage`/`sendUserMessage` with `deliverAs` + `triggerTurn`).
- **Channels** (Telegram/REPL) — host consumes `session.subscribe()`; extension `ctx.ui` calls only function in pi's own TUI/RPC modes, so host-facing UX flows through our channel layer instead.
- **Cross-session orchestration** — boundary detection, session registry/recovery, topic resume: ours, on top of `SessionManager`/`AgentSessionRuntime`.
- **Database** — ours (drizzle + node:sqlite).

## Misc verified utilities

`parseFrontmatter`, `withFileMutationQueue(path, fn)` (parallel tool calls mutating the same file MUST use it), `truncateHead/truncateTail/DEFAULT_MAX_BYTES (50KB)/DEFAULT_MAX_LINES (2000)`, `StringEnum` from pi-ai (use instead of `Type.Union` of literals — Google API compat), `isToolCallEventType`, `createCodingTools/createReadOnlyTools/create{Read,Bash,Edit,Write,Grep,Find,Ls}Tool` with pluggable `operations` (remote/sandbox exec) and bash `spawnHook`, `validateToolCall`, run modes (`InteractiveMode`, `runPrintMode`, `runRpcMode`).

API keys: runtime override (`authStorage.setRuntimeApiKey`) → `auth.json` → env (`ANTHROPIC_API_KEY`) → models.json fallback. OAuth (Claude Pro/Max) supported via `/login`-style flows.
