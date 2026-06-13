# Design: Self-Update

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/self-update.md](../feature-specs/self-update.md)
**Status**: Current

## Purpose

This document explains how Tachikoma updates itself in place: how a self-modifying, long-running process safely installs a new version of its own package and re-execs onto it, and how it recovers — automatically rolling back — when a new version fails to come up. The central design concern is testability of a subsystem whose real operations (install a global package, replace the running process) cannot run in a unit test, so the design is built around injectable seams that isolate those operations from the pure decision logic.

## Problem Context

Tachikoma ships as the global npm package `@asermax/tachikoma`. Keeping a self-hosted install current would otherwise require the user to notice releases and run an install command by hand. The risky part is that the agent must modify the very binary it is running and then restart into it — and a bad release must not strand the assistant on a broken build.

**Constraints:**
- The three dangerous operations — fetch the latest version, install a version globally, re-exec the process — are inherently un-unit-testable and have global side effects, so they must be isolated behind interfaces and the logic around them tested against fakes (task seam requirement; DES-003 testing conventions)
- State must survive the re-exec boundary: the "an upgrade is in progress" marker is written by one process and read by its successor. It lives in the extension's namespaced KV state (`src/db/state.ts`) — the SQLite DB persists across the restart — so no dedicated table or drizzle migration is introduced
- An upgrade that fails *after* restart cannot be detected by the upgrading process (it is gone); only the next boot can notice, so rollback is a startup concern, not an upgrade concern
- Auto-update must not loop: a version that rolled back must not be re-notified or re-attempted until something newer supersedes it

**Interactions:**
- Notifications ([notifications.md](notifications.md)): all user-facing output (update available, back online, rollback failed) is emitted on the `notify` app event, not delivered directly
- Scheduler ([core-shell.md](../feature-specs/core-shell.md)): the periodic check is a cron job via `app.scheduler.cron`
- Extension host ([core-shell.md](../feature-specs/core-shell.md)): startup reconciliation runs as an `app.bootstrap` hook, so it executes after all extensions have set up and the notification router is subscribed

## Design Overview

`index.ts` reads the running version (`readInstalledVersion`), constructs the seams (registry, installer, restarter, and a `DevInstallDetector`) and the typed KV state wrapper, then wires the entry points: a cron job running `runCheck`, an `app.bootstrap` hook running `reconcileStartup`, the `upgrade_self` tool backed by `runUpgrade`, and a `restart_self` tool that re-execs in place via the `Restarter` seam without upgrading. Each entry point is a thin orchestrator that calls a pure decision function (`decisions.ts`) and then performs the seam operations the decision dictates.

The decision functions are total, synchronous, side-effect-free transformations over plain data: `decideCheck` (notify / up-to-date / already-notified / loop-guarded / registry-unavailable), `decideUpgrade` (proceed / up-to-date / blocked-failed / registry-unavailable), and `decideStartup` (none / upgradeSucceeded / rollback). `runUpgrade` adds one further gate on top of `decideUpgrade`: if `DevInstallDetector.isDevInstall()` reports the running copy is a linked/source checkout, it returns a `dev-install` status and refuses, since installing a published version over a dev checkout would clobber the working tree. Version ordering is its own pure module (`version.ts`) with an `isNewerVersion` that ignores prereleases and unparseable inputs.

State is three concerns in one KV namespace: `lastCheck` (checker bookkeeping + last-notified version), `upgradeMarker` (the in-progress marker bridging the restart), and `failedVersion` (the loop guard). The marker is the linchpin of rollback: `runUpgrade` writes it *before* installing; `reconcileStartup` reads it on the next boot and compares the marker's `targetVersion` to the version now running. Equal means the upgrade landed (announce, clear marker + guard); unequal means the new version never booted (record the target as failed, reinstall `previousVersion`, clear the marker, re-exec back). The marker is always cleared before any restart, so a rollback that itself fails to boot is seen as a clean boot by the next process rather than looping.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/self-update/index.ts` | Wiring: read version, build seams + state, register cron job, bootstrap hook, `upgrade_self` tool, and `restart_self` tool | `enabled: false` short-circuits setup entirely; the upgrade tool receives a `() => UpgradeDeps` thunk so deps are assembled once and shared |
| `src/extensions/self-update/version.ts` | `parseVersion`, `compareVersions`, `isNewerVersion` | Minimal semver: numeric major/minor/patch; prerelease ranks below release and is never an upgrade target; unparseable → not newer (stay put) |
| `src/extensions/self-update/decisions.ts` | Pure decisions: `decideCheck`, `nextLastCheck`, `decideUpgrade`, `decideStartup` | All total functions over plain data; outcome/gate/action const maps with derived types; loop guard wins over notification and upgrade |
| `src/extensions/self-update/state.ts` | Typed wrapper over the KV namespace: lastCheck, upgrade marker, failed version | Thin accessors only — no logic; three concerns share one namespace |
| `src/extensions/self-update/seams.ts` | The three seam interfaces + thin real implementations and `readInstalledVersion` | `RegistryClient`/`Installer`/`Restarter` are the only I/O; real impls use `fetch`, `execFile`, and `spawnSync`-then-exit |
| `src/extensions/self-update/checker.ts` | `runCheck`: registry → `decideCheck` → emit + persist | Registry failure leaves bookkeeping untouched; notify only on the `notify` outcome |
| `src/extensions/self-update/upgrade.ts` | `runUpgrade`: gate → dev-install check → marker → install → restart | Marker written before install; a dev install short-circuits with a `dev-install` status (no marker/install/restart); install failure clears marker + records loop guard; restart does not return on success |
| `src/extensions/self-update/startup.ts` | `reconcileStartup`: `decideStartup` → announce / rollback | Marker cleared before any restart; rollback records the target as failed first; rollback-install failure emits an urgent notice and stays put |
| `src/extensions/self-update/tools.ts` | `upgrade_self` and `restart_self` agent tools | Both return text only on a non-restart outcome; prompt guidance warns the agent there is no result on success. `restart_self` re-execs in place via the `Restarter` seam without upgrading (for config/state changes) |

### Seams

The task's central constraint is realised by three interfaces in `seams.ts`:

- **`RegistryClient.fetchLatest(): Promise<string | null>`** — the real `NpmRegistryClient` reads `dist-tags.latest` from `https://registry.npmjs.org/@asermax/tachikoma` with a timeout and returns null on any failure.
- **`Installer.install(version): Promise<void>`** — the real `CommandInstaller` runs the configured command template (`{version}` substituted) via `execFile`; rejects on non-zero exit.
- **`Restarter.restart(): never`** — the real `ProcessRestarter` re-execs by `spawnSync(process.execPath, process.argv.slice(1), { stdio: "inherit" })` and then `process.exit` with the child's code (Node has no `execv`; running the child synchronously in-line keeps a single live process and inherits the terminal). Backs both `upgrade_self` (after install) and `restart_self` (in place).
- **`DevInstallDetector.isDevInstall(): Promise<boolean>`** — the real `NpmGlobalDevInstallDetector` resolves npm's global root and reports whether the running package path is a real global install directory or a linked/source checkout; on detection failure it conservatively returns true (treat as development) so a self-upgrade never clobbers a working tree. Gates `runUpgrade`.

Every orchestrator (`runCheck`, `runUpgrade`, `reconcileStartup`) takes these as dependencies, so tests inject fakes — a registry returning a fixed string, an installer spy, a restarter that throws a sentinel to assert it was reached — and exercise the full control flow without any real I/O.

## Key Decisions

### Seams isolate the dangerous ops; decisions are pure

**Choice**: Split the subsystem into pure decision functions (`version.ts`, `decisions.ts`) and thin orchestrators (`checker.ts`, `upgrade.ts`, `startup.ts`) that wire three injectable seams (`RegistryClient`, `Installer`, `Restarter`). The real seam implementations contain no branching beyond error handling.
**Why**: Installing a global package and re-execing the process cannot run in a unit test and have machine-global side effects. Pushing every decision (is this newer? should we notify? proceed/rollback?) into total, synchronous functions makes the logic exhaustively testable, and keeps the untestable code so thin that "tested logic + thin shell" gives high confidence.
**Alternatives Considered**: One class performing fetch/compare/install/restart with internal methods stubbed in tests via mocking the child-process module (brittle, couples tests to Node internals); shelling out to `npm` for both the version check and install (an extra process per check, and no clean seam boundary).
**Consequences**:
- Pro: The check decision, upgrade gate, rollback decision, and loop guard are all unit-tested against fakes with no real network or process calls
- Pro: Swapping npm for pnpm/bun is a config string, not code
- Con: The thin real seams (registry parse, restart mechanics) are not unit-tested and rely on integration/manual verification

### Marker in KV state bridges the restart; rollback is a startup concern

**Choice**: Persist an upgrade marker (`previousVersion`, `targetVersion`, `startedAt`) in the extension's namespaced KV before installing, and reconcile it on the next boot by comparing `targetVersion` to the running version. Rollback lives in `reconcileStartup`, not in `runUpgrade`.
**Why**: A failure that only manifests after the new code restarts is invisible to the upgrading process — it has already been replaced. Only the successor process can observe "I was supposed to be the target but I'm not / I crashed last time." SQLite survives the re-exec, so the KV namespace is the natural bridge and needs no new table or migration. The marker is cleared before any restart so a rollback that also fails to boot is seen as a clean boot rather than an infinite loop.
**Alternatives Considered**: `/tmp` marker files (extra filesystem surface, no transactional store, lost on reboot); a health-check RPC back to the old process (the old process is gone); a dedicated `app_state`-backed table with its own migration (the KV namespace already exists for exactly this).
**Consequences**:
- Pro: Rollback is automatic and survives crashes mid-upgrade; no migration added
- Pro: Marker-cleared-before-restart bounds recovery to a single rollback attempt
- Con: Detecting "target is not running" relies on the version string differing; a target that boots far enough to run reconciliation but is otherwise broken is treated as a success (the version matches). Deeper health checks are out of scope

### Loop guard suppresses both notify and upgrade until superseded

**Choice**: A rolled-back version is recorded as `failedVersion`; `decideCheck` and `decideUpgrade` both treat it as "do not surface / do not attempt" while it is the latest, and a successful boot clears it.
**Why**: Without this, every check would re-notify the broken version and every upgrade attempt would reinstall it, fail, and roll back — a tight loop. Guarding by exact version (rather than a permanent disable) means a newer fix is offered and applied normally as soon as it is published.
**Alternatives Considered**: A retry counter / backoff (more state, still re-attempts a known-broken build); permanently disabling auto-update after one failure (too blunt — blocks the fix release too).
**Consequences**:
- Pro: No update loop; the broken version is skipped but its successor is not
- Pro: The guard is one version string, cleared automatically on the next healthy boot
- Con: If the registry's latest is pinned at the broken version indefinitely, the user is never re-nudged — by design, but it relies on a fix being published to resume

### Prereleases never auto-upgrade

**Choice**: `isNewerVersion` returns false whenever `latest` carries a prerelease suffix, and unparseable versions are never "newer."
**Why**: Auto-notifying or auto-installing a `-rc`/`-beta` onto a user's live assistant is surprising and risky; stable releases are the only safe automatic target. Being conservative on unparseable input keeps the process from acting on data it cannot reason about.
**Consequences**:
- Pro: The user is only nudged toward, and can only upgrade to, stable releases
- Con: Opting into a prerelease requires a manual install outside this extension
