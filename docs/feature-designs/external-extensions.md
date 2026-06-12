# Design: External Extensions

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/external-extensions.md](../feature-specs/external-extensions.md)
**Status**: Current

## Purpose

Explain how out-of-tree extensibility works on the pi stack: why third-party code loads through the same `defineExtension` contract as first-party features, how sources are resolved and validated, and why the install tooling is restart-to-apply.

## Problem Context

The Python codebase grew a dedicated plugin system: `tachikoma-plugin.toml` manifests, namespaced skill registration, typed manifest config, per-contribution discovery (hooks, events, context providers, post-processors), version tracking, and runtime install with registry refresh. On this stack that machinery is unnecessary: DES-001 makes *every* feature an extension, so out-of-tree loading reduces to "import a `defineExtension` module and hand it to the host". The `external` extension is that bridge — and is itself just another extension.

**Constraints:**
- DES-001: one extension contract for first- and third-party code; third-party extensions use `app.state` for persistence (no migration access)
- Node >= 22 runs `.ts` sources directly, so modules load via native dynamic `import()` — no bundling, transpiling, or jiti step
- The host's `app.registerExtension` enqueues extensions into the same startup pass (core-shell R13); there is no unload/teardown protocol for process-scoped registrations
- A broken third-party module must never abort startup

**Interactions:**
- Core shell ([core-shell](../feature-specs/core-shell.md)): host queue, per-extension config validation, namespaced `KeyValueState`
- Agent sessions: install management tools register through `app.agent.use` like any other tool factory (DES-002)
- Workspace: clones live under `app.workspace.dataDir` (`{workspace}/.tachikoma/extensions/<alias>`)

## Design Overview

At setup, `index.ts` builds the candidate set: configured `sources` (home-expanded, workspace-resolved) unioned with the paths of agent-installed records from `InstallManager.list()`, deduplicated by path. Each candidate goes through `loadExtensionModule` — resolve to a module file, `import()` it, validate the default export's shape — and survivors are handed to `app.registerExtension`, which the host loads after the first-party list in the same pass. `InstallManager` owns the install records in the extension's KV state and the git operations; the four agent tools are thin handlers over it. Nothing is hot-loaded: installs, updates, and uninstalls only change what the *next* startup loads.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/external/index.ts` | `defineExtension` wiring: build source set, load and register modules, expose tools | Config `sources` defaults to `[]`; configured + installed paths deduplicated via a `Set` |
| `src/extensions/external/loader.ts` | `resolveExtensionModule` (file or dir → `index.ts`/`index.js`), `validateExtensionShape`, `loadExtensionModule` | Native `import(pathToFileURL(...))`; every failure mode returns `null` with a warning — never throws into startup |
| `src/extensions/external/installs.ts` | `InstallManager`: install/update/uninstall/list over KV-state records; `isGitSource`; alias validation | Git sources cloned to `{dataDir}/extensions/<alias>`; local paths recorded in place; install validates the module loads before persisting |
| `src/extensions/external/tools.ts` | Param schemas and handlers for `install_extension`, `update_extension`, `list_installed_extensions`, `uninstall_extension`; pi factory | Every mutating response appends the restart note |
| `tests/external/loader.test.ts` | Shape validation, resolution, and error-isolation coverage with real temp modules | |
| `tests/external/installs.test.ts` | Full install/update/uninstall lifecycle against real local git repositories (`file://` URLs) | |

## Key Decisions

### The `defineExtension` contract replaces the manifest plugin system

**Choice**: Third-party extensions are ordinary `defineExtension` modules — the same shape as `src/extensions/<name>/index.ts` in-tree. Validation checks only that shape (`name`, `setup`, optional `configSchema`); there is no manifest file, no contribution declarations, no namespacing layer.
**Why**: DES-001 already gives extensions everything the plugin system provided through bespoke manifest sections — config (`configSchema` + `[extensions.<name>]`), startup hooks (`app.bootstrap`), context providers (`app.agent.provideContext`), post-processors (`app.sessions.registerProcessor`), events (`app.events`), tools (`app.agent.use`). A second declaration format would duplicate that surface and drift from it.
**Alternatives Considered**: Porting the Python manifest system (rejected: parallel API to maintain); pi's own `packages`/`extensions` settings (rejected: those load *pi* session-scoped extensions only, not process-scoped Tachikoma features).
**Consequences**:
- Pro: One authoring story — DES-002 applies verbatim to third-party authors; reading any first-party extension teaches the format
- Pro: Third-party config is validated exactly like first-party config
- Con: Capabilities gated by manifest declarations in Python (e.g. skill-only plugins) now get the full AppContext — trust is all-or-nothing
- Con: Python-style namespacing (`<alias>:<skill>`) is gone; name collisions between extensions are not mediated

### Restart-to-apply lifecycle instead of runtime install

**Choice**: `install_extension`/`update_extension`/`uninstall_extension` only mutate records and files on disk; the loaded extension set changes on the next startup. Tool responses state this explicitly.
**Why**: Extension `setup(app)` registers process-scoped services — scheduler jobs, bootstrap hooks, processors, session factories — and the host has no unregister protocol. Runtime swap would need teardown bookkeeping for every AppContext service (the bulk of the Python plugin system's update/re-registration complexity). Eager validation at install time (`loadExtensionModule` before persisting) keeps the failure visible in the tool call rather than at the next boot.
**Consequences**:
- Pro: No partial-registration states; startup is the single composition point
- Pro: Install errors surface immediately to the agent, with clone cleanup on failure
- Con: The user must restart Tachikoma to activate, update, or fully remove an extension

### Install records in KV state, not in the user's config file

**Choice**: Agent-driven installs persist as a `Record<alias, InstallRecord>` under the `installs` key of the extension's namespaced `app.state`; the `[extensions.external].sources` config list stays user-owned and read-only.
**Why**: Writing config.toml from a tool (the Python approach) makes the agent edit a user-owned file, with comment/format preservation problems. KV state is the DES-001 home for exactly this kind of structured-but-small extension state, and keeps the two provenances (user-declared vs agent-installed) separable.
**Consequences**:
- Pro: `config.toml` is never machine-mutated; uninstall cannot corrupt user config
- Pro: `list_installed_extensions` distinguishes agent installs naturally
- Con: Two places define the load set (config + state); the deduplicated union in `index.ts` reconciles them

### Source kind inferred from URL shape, no ref pinning

**Choice**: `isGitSource` classifies by pattern (`https?://`, `git@`, `ssh://`, `file://`, or `.git` suffix); git sources are shallow of ceremony — plain `git clone`, updated with `git pull --ff-only` on the default branch. Everything else is a local path loaded in place.
**Why**: Covers the practical cases with one regex and two git commands. The Python system's refs, commit-SHA version tracking, daily `ls-remote` checks, and URL-archive sources bought precision this stage of the rewrite does not need.
**Consequences**:
- Pro: Minimal surface; `--ff-only` prevents silent merge states in clones
- Con: No branch/tag selection, no update notifications, no integrity pinning — updates are manual and trust the remote's default branch

## System Behavior

### Scenario: Configured source loads at startup

**Given**: `[extensions.external] sources = ["~/exts/weather"]` and that directory holds an `index.ts` exporting a valid extension.
**When**: The host runs `external`'s setup (last in the first-party list).
**Then**: The module is imported and validated, `app.registerExtension` enqueues it, and the host runs its `setup(app)` in the same pass — with `[extensions.weather]` config validated against its schema and a `weather`-namespaced `app.state`.

### Scenario: Broken module is isolated

**Given**: One configured source throws at import time.
**When**: Setup iterates the source set.
**Then**: A warning logs the source and error, that source yields nothing, the remaining sources load, and startup completes.

### Scenario: Git install lifecycle

**Given**: The agent calls `install_extension` with a git URL and alias `demo`.
**When**: The clone lands in `{dataDir}/extensions/demo` and validates as a loadable extension.
**Then**: The record persists in KV state and the response notes the restart requirement. A later `update_extension("demo")` fast-forwards the clone; `uninstall_extension("demo")` deletes the clone and the record. If the clone had contained no valid module, it would have been removed and nothing recorded.

## Notes

- Deliberately not carried over from the Python plugin system: manifest config schemas, namespaced skills, Claude Code plugin compatibility, version/update-status tracking, daily update checks, URL-archive sources, and runtime registry refresh
- `external` is last in `src/extensions/index.ts`, so third-party setups run after every first-party setup and may rely on their services (DES-001 ordering rule)
- Uninstalled extensions remain active until restart — there is no unload
