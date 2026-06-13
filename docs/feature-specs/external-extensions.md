# External Extensions

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Out-of-tree Tachikoma extensions load through the exact same `defineExtension` contract as first-party features (DES-001): a module whose default export has a `name`, a `setup(app)` function, and optionally a TypeBox `configSchema`. The `external` extension imports such modules at startup from paths declared in config and from agent-installed records, validates their shape, and registers them with the host. Agent tools install (git clone or local path), update, list, and uninstall third-party extensions; changes take effect on restart.

There is no separate manifest-based plugin system (manifests, namespaced skill registration, plugin-specific pipelines): the unified extension API is the single out-of-tree extensibility surface. Third-party extensions receive the full AppContext and persist state through `app.state` — they have no access to drizzle migrations.

## User Stories

- As a user, I want to declare third-party extensions in my config so that Tachikoma loads them at startup alongside built-in features
- As a third-party author, I want to ship a single `defineExtension` module — the same contract first-party features use — so that no separate plugin manifest or wiring format exists to learn
- As the agent, I want install/update/list/uninstall tools so that I can manage third-party extensions on the user's behalf

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Load out-of-tree extensions satisfying the `defineExtension` contract (DES-001); no separate plugin/manifest system exists |
| R1 | `[extensions.external]` config declares `sources`: an array of paths (file or directory); `~` is expanded and relative paths resolve against the workspace root. It also declares `setupTimeoutMs` (default 30000): the hang guard applied to each third-party `setup` |
| R2 | Agent-installed extensions (records in the extension's namespaced KV state) load alongside configured sources; the combined set is deduplicated by path |
| R3 | A source resolves to its module file: a `.ts`/`.js` file directly, or `index.ts`/`index.js` inside a directory; anything else is not loadable |
| R4 | The module's default export is validated before registration: non-empty `name` string, `setup` function, and (when present) an object `configSchema`; invalid exports are rejected with a stated reason |
| R5 | Third-party extensions are error-isolated across their whole startup, not just loading: unresolvable sources, import failures, and invalid shapes are logged and skipped (load phase); a third-party `setup` that throws or hangs past `[extensions.external].setupTimeoutMs` (default 30000) is logged as a warning and skipped (setup phase); and a bootstrap hook a third-party extension registers is isolated when hooks run — one bad extension never aborts startup. First-party (built-in) extensions are *not* isolated: their `setup` and bootstrap-hook failures propagate so core bugs surface (bootstrap matches core-shell R14) |
| R6 | Valid extensions register via `app.registerExtension` and load in the same startup pass after the first-party list (see [core-shell](./core-shell.md) R12/R13), receiving the full AppContext including `[extensions.<name>]` config validated against their `configSchema` |
| R7 | Third-party extensions persist via `app.state` (namespaced key-value store); they have no migration access |
| R8 | `install_extension(source, alias)`: git sources (`https://`, `git@`, `ssh://`, `file://` prefixes or a `.git` suffix) are cloned into `{workspace}/.tachikoma/extensions/<alias>`; other sources are recorded in place (no copy); the alias must match `[a-z0-9][a-z0-9-]*` and must not collide with an existing install |
| R9 | Installation verifies the source contains a loadable, valid extension module before recording it; a failed git install removes the clone and persists nothing |
| R10 | `update_extension(alias)` runs `git pull --ff-only` for git installs and reports local installs as always current; unknown aliases return an error |
| R11 | `uninstall_extension(alias)` removes the install record, deleting the cloned directory for git installs while leaving local source directories untouched |
| R12 | `list_installed_extensions` reports each install's alias, source kind (git/local), source, path, and install time. (The rendered output heads the list with "# Installed Plugins" and the tool's display label is "List Installed Plugins" — a "Plugins" wording that diverges from the "extensions" terminology used everywhere else) |
| R13 | Install, update, and uninstall take effect on the next restart; tool responses say so explicitly |

## Behaviors

### Source Resolution and Validation (R3, R4, R5)

A source path becomes a registered extension only if it resolves to a module whose default export satisfies the contract.

**Acceptance Criteria**:
- Given a directory containing `index.ts` with a valid default export, when loaded, then the extension is returned with its `name` and `setup`
- Given a `.ts` file path with a valid default export, when loaded, then the extension is returned
- Given a missing path, a non-module file (e.g. `readme.md`), or a directory without an index module, when loaded, then `null` is returned and a warning names the source
- Given a module whose default export lacks `name` or `setup`, or whose `configSchema` is not an object, when loaded, then it is skipped with a warning containing the validation reason
- Given a module that throws at import time, when loaded, then it is skipped with a warning carrying the error

### Startup Isolation (R5)

A third-party extension is isolated across its entire startup — both its `setup` and any bootstrap hook it registers; a built-in one is not.

**Acceptance Criteria**:
- Given a registered third-party extension whose `setup` throws, when the host runs it, then a warning names the extension, that extension is skipped, and the remaining extensions and startup proceed
- Given a third-party `setup` that never settles, when it exceeds `setupTimeoutMs`, then it is treated as hung, logged, and skipped, and startup proceeds
- Given a third-party extension that registered a bootstrap hook which throws, when bootstrap hooks run, then the hook is logged and skipped and the remaining hooks proceed
- Given a first-party (built-in) extension whose `setup` or bootstrap hook throws, when the host runs it, then the error propagates and aborts startup (core bugs are not swallowed; bootstrap matches core-shell R14)

### Startup Loading (R1, R2, R6, R7)

**Acceptance Criteria**:
- Given `sources = ["~/exts/foo", "rel/bar"]`, when `external` sets up, then `~` is expanded, `rel/bar` resolves against the workspace root, and each resolved source is loaded
- Given an agent-installed record and a configured source pointing at the same path, when setup runs, then the path is loaded once
- Given a valid external extension, when registered, then the host runs its `setup(app)` in the same startup pass (after the first-party list) with `app.extensionConfig` validated against its `configSchema`
- Given one source is invalid, when setup runs, then the remaining sources still load and startup proceeds

### Installing (R8, R9, R13)

**Acceptance Criteria**:
- Given `install_extension` with a git source and a free alias, then the repository is cloned into `{workspace}/.tachikoma/extensions/<alias>`, the record (source, path, install time) is persisted, and the response notes the restart requirement
- Given a local path source, then the path is recorded as-is — nothing is copied into the extensions directory
- Given an alias with uppercase letters or spaces, then the tool returns an "Invalid alias" error
- Given an alias already in use, then the tool returns an "already installed" error
- Given a git source whose clone contains no valid extension module, then the clone is removed, no record is persisted, and the error explains the expected module shape

### Updating, Uninstalling, Listing (R10, R11, R12, R13)

**Acceptance Criteria**:
- Given `update_extension` on a git install with new upstream commits, then `git pull --ff-only` brings the clone current and the response notes the restart requirement
- Given `update_extension` on a local install, then the response reports it is always current (loaded in place)
- Given `uninstall_extension` on a git install, then the cloned directory is deleted and the record removed; on a local install, only the record is removed and the source directory is untouched
- Given `update_extension` or `uninstall_extension` with an unknown alias, then a "not installed" error is returned
- Given `list_installed_extensions` with no installs, then "No external extensions installed." is returned; with installs, each entry shows alias, source kind, source, path, and install time
