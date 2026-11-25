# ADR-016: Minimum Release Age for Dependencies

**Status**: Accepted
**Date**: 2026-11-25

## Context

Tachikoma takes every dependency from the npm registry, and so does everything those dependencies depend on. The lockfile holds several hundred packages, and only 27 of them are direct dependencies: the rest arrive through someone else's package.json and are reviewed by nobody here. A compromised or defective release is usually discovered and pulled within hours, but an install that resolves on the day of such a release takes it like any other.

pnpm 11 has a setting for this, `minimumReleaseAge`: the number of minutes that must pass after a version is published before pnpm installs it. It applies to every package, transitive ones included, and pnpm checks the lockfile against it on every install. One day is pnpm's built-in default. What the default does not give is enforcement. The setting has two modes, and which one applies depends only on whether the setting is written down:

- **Implicit, the default.** The gate is loose. When a range has no version that is old enough, pnpm resolves a younger version anyway and appends it to `minimumReleaseAgeExclude` in `pnpm-workspace.yaml`. The policy then rests on an exclusion list that pnpm itself keeps adding to.
- **Explicit.** The gate is strict (`minimumReleaseAgeStrict` is on unless set otherwise). A range with no version that is old enough fails the install.

The package manager is pnpm 11, pinned through `packageManager` ([ADR-002](ADR-002-runtime-and-package-manager.md)), and CI installs the lockfile frozen ([ADR-013](ADR-013-release-tooling.md)).

## Decision

Every version that enters the lockfile is at least one day old.

- `pnpm-workspace.yaml` sets `minimumReleaseAge: 1440` explicitly, one day in minutes. pnpm's strict mode is on by default.
- "Latest" means the newest version that is a day old when the lockfile is resolved. A release younger than that waits until the next day; it is not forced through.
- No release-age exclusions are kept. `minimumReleaseAgeExclude` has no place in the file: no entry is added by hand, and none is approved at pnpm's interactive prompt, which is strict mode's other way out.

Under this setting pnpm resolves each range to its newest version published before the cutoff. A range that has none fails outside an interactive terminal, and in CI, with `ERR_PNPM_NO_MATURE_MATCHING_VERSION`, and writes nothing. At an interactive terminal pnpm asks first whether to accept those versions and record them as exclusions; declining ends the install with `ERR_PNPM_MINIMUM_RELEASE_AGE_DENIED`. Every install checks the lockfile against the same rule, a frozen install included, and an entry younger than the cutoff fails it with `ERR_PNPM_MINIMUM_RELEASE_AGE_VIOLATION`.

## Consequences

### Positive

- A release has a day to be pulled, or to show its defects, before it can reach this repository, and the rule covers transitive packages as much as direct ones
- The policy is written in the repository and enforced by the tool: an install that breaks it fails, and nothing is rewritten on the install's behalf
- A lockfile cannot carry a version younger than a day, because a frozen install checks it, so CI enforces the same rule

### Negative

- "Latest" is a day behind. A fix published today enters the lockfile tomorrow at the earliest, and a dependency that releases on the day of an upgrade is left for the next one
- An exact pin can hold up an install: pinned to a version under a day old, it fails resolution until the next day, where a range resolves to the previous version
- The rule is about time, not about trust. A release that is pulled within the day is kept out; a compromise that goes unnoticed for longer passes like any other version

## Alternatives Considered

- **The implicit default**: the same one day, but loose. pnpm would resolve a younger version whenever a range has no older one and append it to `minimumReleaseAgeExclude` itself, so the file would collect exclusions nobody decided on, and the gate would hold only until the first such range
- **One week**: a longer buffer, paid for on every upgrade. An upgrade of all dependencies could take each dependency only up to the version that is a week old, and a required version younger than that, such as a new major that another dependency has moved to, would make the resolve fail until it aged. Transitive packages included, this can hold a whole upgrade back for days. A day covers the time in which a bad release is found and pulled; the days after it delay fixes without adding protection
- **Per-version exclusions for reviewed packages**: an exclusion list that has to be maintained by hand. Each entry is a bypass of the rule, most entries would name transitive packages nobody reviews, and an entry outlives the version it was written for

## Notes

- Related: [ADR-002](ADR-002-runtime-and-package-manager.md) (pnpm as the package manager), [ADR-013](ADR-013-release-tooling.md) (CI installs the lockfile frozen).
- Where the behavior was read: pnpm 11.3.0's own distribution, in which `minimum-release-age` defaults to `24 * 60` minutes, `minimumReleaseAgeStrict` turns on when `minimumReleaseAge` is configured explicitly, and the policy handlers in `installing/commands/lib/policyHandlers.js` write the exclusions in loose mode and fail or prompt in strict mode. And the pnpm settings reference, for `minimumReleaseAge` and `minimumReleaseAgeStrict`.
- Why not one week, measured: with a seven-day gate, the lockfile of this repository's dependency upgrade, resolved on 2026-11-25, would have been unresolvable until 2026-11-29, because pi 1.6.0's AWS SDK chain and grammy 1.48.0 were younger than a week.
- Revisit if pnpm changes the default or what strict mode does, or if the project moves to a registry with its own vetting. A same-day security fix is not an exception to this decision: the choice is to wait a day, or to accept an exclusion knowingly and on the record.
