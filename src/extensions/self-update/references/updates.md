# Updates

In-app upgrades of Tachikoma itself. Owned by the self-update extension.

## Flow

1. A scheduled version check (`checkCron`, default daily at 10:00) compares the installed
   version against the npm registry and notifies the person when one is available — you
   never need to poll.
2. `upgrade_self` installs the latest version using `installCommand` and re-execs — but
   only after the current exchange completes, so your in-flight response is fully delivered.
3. On the next boot, an in-progress upgrade marker is reconciled: success is announced with
   a "back online" notification; a failed upgrade is rolled back automatically.

`restart_self` re-execs on the current version (same delivery guarantee).

Why the dedicated tools only: a shell `npm`/`pnpm` install would race the running process,
bypass the marker/rollback bookkeeping, and leave the person with a broken install and no
report.

## Configuration

`[extensions.self-update]`: `enabled` (default `true`); `checkCron` (default `"0 10 * * *"`);
`installCommand` (default installs `@asermax/tachikoma` globally).
