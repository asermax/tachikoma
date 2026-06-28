# ADR-015: Subagent extension-tool grants via a source-agnostic binding mechanism

**Status**: Accepted
**Date**: 2026-06-28

## Context

A delegated subagent (`delegate_to_agent` → `SideRunner.run`) runs in a **bare, isolated, in-memory pi session**. The session-scope / factory-binding model that governs which extension factories bind into a session was two-valued (`main` / `background`) with no ADR of its own, and a bare session binds no Tachikoma factories.

Delegations are tool-starved by a **double-block**:

1. `selectExtensionFactories` returns `[]` for a bare session — no factory binds, so a factory-registered tool (e.g. a Firecrawl scrape/search extension) scoped to `main` is unreachable.
2. The hard `tools` allowlist pi applies at session creation admits only the built-in names the run lists (`read`, `grep`, `find`, `ls`, `bash`, `edit`, `write`), masking any tool that *does* register — including **pi-native extensions**, which pi loads via `agentDir` discovery even into a bare session.

The assistant therefore cannot hand tool-dependent work — most concretely web research — to a parallel, isolated subagent, and is forced to run that work on the main session, burning main-session context.

**Constraints that shaped the decision:**

- Isolation is preserved: the run stays in-memory, prompt-isolated (`isolatePrompt: true`), and disposed after the run. Granted tools execute inside that boundary, never in the main session.
- Read-only built-ins stay the default; extension tools are opt-in per delegation and never granted by default.
- The grant must be **source-agnostic**: a requested name resolves whether its provider is a Tachikoma factory opted into the new scope *or* a pi-native extension loaded from `.pi` / packages / settings.
- No secrets plumbing: a granted tool reaches its credentials the same way it does in the main session. No operator allowlist/denylist (out of scope).
- The new scope must not enable delegation recursion — a subagent must not itself be able to delegate.

## Decision

Add a **third session scope**, `subagent` (`SESSION_SCOPES.subagent`), meaning a delegated subagent run that requested extension tools. It threads through the existing scope machinery exactly as `background` did — `factoryBindingTargets` resolves it, `app.agent.use(f, { sessionScopes: […, "subagent"] })` routes the factory into a third list (`subagentFactories`), and a new `bindSubagentFactories` open-option makes `selectExtensionFactories` bind the `subagentFactories` with `{ scope: "subagent" }`. Opt-in granularity is **per-factory**, matching the scope model (scopes are a factory-level property); the three session types (main/background/subagent) remain mutually exclusive at open time, with background precedence preserved.

Resolve requested tool names **source-agnostically at execute time**, against the *opened subagent session's* `AgentSession.getAllTools()` — a single membership check covers a Tachikoma-factory tool and a pi-native extension tool identically. An unresolved name throws a self-correcting error (of the same "list what is valid" form as the unknown-agent / unknown-built-in-tool errors) listing the grantable (non-builtin) names, **before any prompt runs**.

Narrow the active set with `AgentSession.setActiveToolsByName([...resolvedBuiltins, ...granted])`. To do this, the grant path opens the session **without** a `tools` allowlist (so every bound factory tool registers and is enumerable, mirroring the existing background path's "drop the allowlist so factory tools survive"), validates, then narrows to exactly the resolved built-ins plus the granted tools.

A **pi-native extension is grantable by virtue of being installed** — installing an extension under `.pi` (or via settings/packages) is already a trust act that binds it into every session pi opens; the grant merely stops masking tools it already registered. There is no Tachikoma-side opt-in for pi-native extensions, because they cannot call `app.agent.use`.

The mechanism reuses primitives pi already exposes (`getAllTools()` / `setActiveToolsByName()`); no new binding mechanism is introduced.

## Consequences

### Positive

- One source-agnostic code path covers Tachikoma-factory and pi-native tools; no per-source branching and no tool-name declaration contract for exposing factories to maintain.
- Validation is precise — each name is checked against the subagent session's actual registry, not a cached or declared list.
- The `delegate_to_agent` description never goes stale: it advertises a capability generically rather than enumerating tool names.
- The read-only built-in default is restored cleanly; the grant reuses the background path's "drop the allowlist so factory tools survive" pattern.
- pi-native extensions become grantable with zero author-side changes.
- Recursion stays structurally impossible without a runtime guard: the skills/delegate factory is scoped `["main", "background"]` and deliberately not `"subagent"`, so a subagent session never binds `delegate_to_agent`.

### Negative

- **Generic advertising**: the description cannot enumerate grantable names (they are only knowable after a session opens), so the model must know or name a tool to request it. Mitigated by `promptGuidelines` naming the common case (web research → web tools) and by the self-correcting error listing the grantable set on a miss.
- **Ambiguity is best-effort**: pi's tool registry dedupes by name, so when two providers expose the same name only the survivor appears in `getAllTools()` — a collision is undetectable (especially for pi-native providers, which receive their own unwrapped `pi` from pi's loader). Resolution is therefore **last-wins, documented** — not an ambiguity error.
- **All-or-nothing per factory**: opt-in is per-factory, so an author cannot expose a strict subset of a factory's tools.
- **No per-extension gate for pi-native tools**: any installed pi-native extension is grantable to any delegation. This is bounded by installation already being a trust boundary and by the operator allowlist/denylist being explicitly out of scope.
- **Built-in identification is duplicated**: the unresolved-name listing tells built-ins apart from extension tools with a local `BUILTIN_TOOL_NAMES` set in `src/agent/side-run.ts`, not `sourceInfo.source === "builtin"`, because the agent layer must not import from the skills extension and a name-only test fake carries no `sourceInfo`. Tracked for a future single-source reconciliation if pi's built-in surface changes.
- **Credentials are deliberately out of scope**: a granted tool reaches its credentials the same way it does in the main session; there is no secrets plumbing and no operator allowlist/denylist.
- Between `open` and `setActiveToolsByName` the session briefly has pi's default built-ins active — irrelevant, since no prompt runs until after narrowing.

## Alternatives Considered

- **Per-tool declaration** (`app.agent.use(f, { sessionScopes: ["subagent"], tools: ["web_search"] })`): duplicates the scope concept with a per-tool list the author must keep in sync with what the factory actually registers, and buys little — an extension exposing one tool to subagents almost always exposes all of them. Rejected for complexity; per-factory opt-in matches the scope model.
- **Re-scoping individual tools** rather than the factory: there is no per-tool scope concept in the binding model, so this would invent one — the same downside as per-tool declaration.
- **A separate `app.agent.exposeToSubagents(…)` registration surface**: rejected — it would parallel `app.agent.use` without adding any power over a third scope value.
- **Selective factory binding** (bind only the factories that own a requested tool): rejected — binding is just registration (cheap; credentials are touched at tool execution, not registration), and bind-all-then-narrow is simpler while still leaving non-granted tools inert via `setActiveToolsByName`.
- **An `excludeTools` denylist**: requires enumerating the entire universe of tools to deny (every other bound factory tool plus unwanted built-ins), which is more fragile than naming the small desired set.
- **Registration-time tool-name declaration for advertising** (declare names so the description can enumerate grantables precisely): rejected — a declaration contract that must be kept in sync, that cannot cover pi-native extensions (they never call `app.agent.use`), and that the spec sanctions generic advertising for. Validation still has to happen at execute time for pi-native names, so declaration only helps advertising.
- **Resolving against the main session's tool set** (the main session is already open, so `getAllTools()` is cheap there): rejected — the main session binds `main`-scoped factories, not `subagent`-scoped ones, and a pi-native extension need not be bound to the main session, so the main session is the wrong universe for validating subagent grants.
- **An inclusive `tools` allowlist** (name extension tools in the open-time `tools` list): viable since `tools` accepts any name, but it silently ignores an unresolved name rather than throwing, so a separate validation pass is still needed — and once that pass exists, `setActiveToolsByName` is the cleaner narrowing primitive.
- **An operator allowlist/denylist for subagent grants** and **a manifest convention for pi-native extensions to declare exposure**: explicitly out of scope — no mechanism exists for pi-native extensions to speak to Tachikoma, and inventing one contradicts "installed = trusted."

## Notes

- Related: [ADR-001](ADR-001-agent-sdk.md) (the pi agent SDK and the factory-binding model this extends), [ADR-014](ADR-014-session-source-of-truth.md).
- The grant path leans on pre-1.0 `AgentSession` primitives (`getAllTools()`, `setActiveToolsByName()`, `dispose()`), all synchronous on the installed SDK. These are tracked in `docs/reference/pi-sdk-notes.md` and must be re-verified on a pi upgrade.
