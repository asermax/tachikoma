# Self-Update

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The self-update extension keeps a globally-installed Tachikoma current with its published npm package (`@asermax/tachikoma`). On a configurable cadence it compares the running version against the latest published version on the npm registry and, when a newer stable release exists, emits a `notify` event so the user hears about it. An `upgrade_self` agent tool installs the latest version globally and re-execs the process so the new code takes over. Because the process modifies and then restarts itself, every upgrade records an "in progress" marker beforehand; on the next boot the extension reconciles that marker — announcing success, or automatically rolling back to the previous version when the new one fails to come up — and a loop guard prevents re-attempting a version that just rolled back. A `restart_self` tool re-execs the current version in place (for config or state changes) and records its own marker beforehand, so the next boot announces a "back online" notification confirming the restart completed. The re-exec is deferred until the current exchange completes, so the agent's response is delivered in full and post-exchange work (exchange processors, held-delivery drain) runs before the process is replaced. The extension also contributes a usage context section (scope: main only) covering the update/restart tools and the don't-run-npm-directly rule, with rollback and check-cadence detail in its reference file (`references/updates.md`, per [DES-014](../design/DES-014-two-tier-agent-facing-documentation.md)).

All dangerous operations (registry fetch, global install, process re-exec) sit behind injectable seams so the version-comparison, check, upgrade-gate, and rollback decisions are pure and unit-tested.

## User Stories

- As a user, I want to be told when a newer Tachikoma is published so that I can decide to upgrade
- As a user, I want to ask the agent to upgrade itself and have it install the new version and restart cleanly
- As a user, I want a botched upgrade to roll itself back automatically so that the assistant never gets stuck on a broken build
- As a user, I want to be told Tachikoma is back online after I ask it to restart, so I know the restart completed
- As a user, I do not want to be repeatedly nudged toward — or to repeatedly fail on — a version already known to be broken on this machine
- As an operator, I want the install command to be configurable so that npm, pnpm, or another global installer can be used

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | The extension is gated by `enabled` (default true); when disabled, setup is a no-op (no scheduler job, no tool, no startup reconciliation) |
| R1 | The running version is read from the package's own `package.json`, resolved relative to the module so it reflects the installed copy, not the cwd; unreadable metadata falls back to `0.0.0` |
| R2 | On a configurable cron cadence (`checkCron`, default daily at 10:00) the extension fetches the latest published version from the npm registry and compares it to the running version |
| R3 | Version comparison is semver-ish: compare major/minor/patch numerically; a prerelease (`-rc.1`, `-beta`) is never treated as an upgrade target, and unparseable inputs never count as newer |
| R4 | When a strictly-newer stable version exists that is not the known-failed version and has not already been notified, emit a `notify` event (source `self-update`, severity `info`) telling the user the current → latest transition |
| R5 | The same newer version is not notified twice; the last-notified version is persisted in the extension's namespaced KV state (no dedicated table / migration) |
| R6 | A registry lookup failure is a quiet no-op for that tick: nothing is notified and the check bookkeeping is left untouched |
| R7 | The `upgrade_self` agent tool upgrades to the latest published version: it gates on the same decision (refuses when already current, when the registry is unavailable, when the target is the known-failed version, or when running from a development install — see R15a), otherwise writes the in-progress marker, installs the target, and schedules a deferred restart (R16) so the current exchange completes before the process re-execs |
| R8 | Before installing, an upgrade marker (`previousVersion`, `targetVersion`, `startedAt`) is persisted; if the install itself throws, the marker is cleared, the target is recorded as the failed version (loop guard), and no restart happens |
| R9 | After a successful install the process re-execs via `spawnSync(process.execPath, process.argv.slice(1), { stdio: "inherit" })` — the script args are `process.argv.slice(1)` (dropping the node executable in `argv[0]`) — and the parent exits with the child's status so the newly-installed version takes over. The re-exec is the deferred one (R16): the coordinator triggers it after the exchange completes, not from inside the tool |
| R10 | On startup the extension reconciles any marker left by a prior boot. A restart marker means a plain `restart_self` completed — announce "back online" and clear it. An upgrade marker means an upgrade was attempted: running the marker's target version means it landed — announce "back online" and clear both the marker and the loop guard; not running it means the new version failed — record the target as failed (loop guard), reinstall the previous version, clear the marker, and re-exec back onto the known-good build. The upgrade marker takes precedence if both are present, so its rollback always runs |
| R11 | The loop guard (`failedVersion`) suppresses both notification (R4) and upgrade (R7) for a version that rolled back, until a strictly-newer version supersedes it; a successful boot clears it |
| R12 | If a rollback's own install fails, no restart happens and an `urgent` notification reports that manual intervention is needed |
| R13 | The global install command is configurable via `installCommand`, a template where `{version}` is substituted with the target version (default `npm install -g @asermax/tachikoma@{version}`) |
| R14 | Every state transition (check outcome, marker write, install, restart, rollback) is logged |
| R15 | A `restart_self` agent tool re-execs the running process in place via the `Restarter` seam *without* upgrading (to pick up config or state changes). The restart is deferred (R16): the tool writes a restart marker, returns a result, the current exchange completes, and only then does the process re-exec; the next boot announces a "back online" notification confirming the restart completed (R10) |
| R15a | `upgrade_self` refuses when the running copy is a development install (detected by `DevInstallDetector.isDevInstall` — e.g. an `npm link` symlink into a checkout, or a source-run; detection failures conservatively treat the install as development), returning a `dev-install` status that tells the user to upgrade manually from their working tree; no marker is written, nothing is installed, and the process is not restarted |
| R16 | A `restart_self` / `upgrade_self` restart is deferred until after the current exchange completes: the tool records a pending-restart request on the coordinator (via `app.requestRestart`) and returns a result instead of re-execing from inside the tool. Once the exchange finishes (response streamed, exchange processors run, held deliveries drained), the coordinator exits its run loop, its graceful-drain teardown runs, and the process re-execs. This guarantees the agent's response is delivered in full and post-exchange work runs before the process is replaced |

## Behaviors

### Version Check and Notification (R2, R3, R4, R5, R6)

The scheduled tick surfaces a newer published version exactly once.

**Acceptance Criteria**:
- Given the registry reports a strictly-newer stable version not previously notified, when the check runs, then a `notify` event (source `self-update`, `info`) with the current → latest text is emitted and the latest version is recorded as notified
- Given the registry reports the same or an older version, then nothing is emitted
- Given the registry reports a newer version that was already notified, then nothing is emitted on the second check
- Given the registry reports a prerelease as latest, then it is not treated as an upgrade and nothing is emitted
- Given the registry lookup fails (null), then nothing is emitted and the check bookkeeping is unchanged

### Upgrade and Restart (R7, R8, R9, R15, R16)

The `upgrade_self` tool installs the latest version and schedules a deferred restart so the new code takes over once the current exchange completes.

**Acceptance Criteria**:
- Given a newer version exists, when `upgrade_self` runs, then the upgrade marker is written, the installer is invoked with the target version, the tool returns a `started` message, and a deferred restart is scheduled (R16) — the process re-execs only after the exchange completes
- Given the running version is already latest, then the tool returns "up to date" without writing a marker, installing, or restarting
- Given the registry is unavailable, then the tool returns a registry-unavailable message and does nothing else
- Given the install throws, then the marker is cleared, the target is recorded as the failed version, no restart happens, and the failure is surfaced
- Given the running copy is a development install (linked or source checkout), when `upgrade_self` runs, then it returns a `dev-install` message advising a manual upgrade and does not write a marker, install, or restart
- Given the user explicitly asks to restart without upgrading, when `restart_self` runs, then the tool writes a restart marker, returns a result, and the process re-execs in place (same version) after the exchange completes; the next boot announces a "back online" notification (R10)
- Given a deferred restart is pending, when the current exchange completes, then the agent's response was streamed in full and exchange processors ran before the run loop exits, the graceful drain flushes any held deliveries, and the process re-execs (R16)

### Startup Reconciliation and Rollback (R10, R11, R12)

On every boot the marker decides success vs rollback.

**Acceptance Criteria**:
- Given no marker, when the extension boots, then nothing happens (clean boot)
- Given a restart marker (and no upgrade marker), when the extension boots, then a "back online" `info` notification is emitted and the restart marker is cleared
- Given both an upgrade marker and a restart marker, then the upgrade marker's success/rollback reconciliation runs and the restart marker is ignored
- Given a marker whose target equals the running version, then a "back online" `info` notification is emitted and both the marker and the loop guard are cleared
- Given a marker whose target does not equal the running version, then the target is recorded as the failed version, the previous version is reinstalled, the marker is cleared, and the process re-execs
- Given a rollback whose reinstall fails, then no restart happens and an `urgent` notification reports that manual intervention is needed

### Loop Guard (R11)

A version that rolled back is never auto-offered or auto-attempted again until superseded.

**Acceptance Criteria**:
- Given a failed version recorded, when the check runs and the registry reports exactly that version as latest, then nothing is notified
- Given a failed version recorded, when `upgrade_self` runs and that version is the target, then the tool refuses with a blocked message and does not install
- Given a failed version recorded, when the registry later reports a strictly-newer version, then that newer version is notified and upgradable normally
- Given a successful upgrade, then the loop guard is cleared on the next boot's success path

### Configuration (R0, R13)

**Acceptance Criteria**:
- Given `enabled: false`, when the extension sets up, then no scheduler job, tool, or bootstrap hook is registered
- Given a custom `installCommand` with `{version}`, when an install runs, then the command is invoked with `{version}` substituted by the target version
- Given a custom `checkCron`, when the scheduler runs, then the check fires on that cadence
