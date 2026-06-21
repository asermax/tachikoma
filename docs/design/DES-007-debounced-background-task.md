# DES-007: Trailing-Edge Debounced Background Task

**Scope**: Project-wide
**Date**: 2026-06-21
**Last Updated**: 2026-06-21

## Pattern

A background job that should run **once after a burst settles**, not once per event, is wrapped in a trailing-edge debounce: each event calls `touch()` to clear and re-arm a `delayMs` timer, and the job runs only once after `delayMs` elapses with no further `touch()`. The job runs **off the caller's path** (the timer callback launches it; `touch()` returns immediately), so a slow job never blocks the event stream that feeds it.

Three properties are mandatory:

- **Single-flight with coalescing.** A fire that becomes due while a previous run is still active is *deferred*, not dropped and not overlapped — exactly one re-run follows the active one, coalescing every touch that piled up during it. Touches thus never overlap executions, yet a touch that arrives mid-run is never silently lost.
- **Cancellable and drainable.** `clear()` cancels the pending timer *and* clears the coalesced re-run flag, so a caller about to do the work itself (a backstop) can hand off cleanly. `whenIdle()` awaits any in-flight run to completion, re-checking after each await so a coalesced re-run is also drained — the seam tests and orderly shutdown use.
- **Disabled when `delay ≤ 0`.** `touch()` is a no-op, so a config value of `0` is the explicit "off" switch with no separate boolean.

The timer is `unref()`-ed so it never keeps the process alive on its own (shutdown owns exit). The job is expected to be **error-tolerant**: the debounce wrapper catches and warns any rejection rather than propagating, because a background job must never crash its host.

## Rationale

Per-event execution of an expensive job (one that runs a model call or does network I/O on every event) is wasteful and would extend every event with that cost. A trailing-edge debounce collapses a burst of events into one execution after the burst settles, and running it off the event path keeps events snappy. Single-flight with coalescing is the subtle part: a naive "drop if busy" loses touches that arrived during a run (the work they represent waits for the *next* event to re-trigger it), and a naive "queue every touch" can pile up unbounded re-runs. Coalescing to one deferred re-run captures any mid-run touches without overlap or unbounded growth.

The `clear()`-also-clears-the-re-run-flag detail matters for backstops: a close/shutdown path that runs the same job authoritatively must both cancel the pending timer *and* forget a coalesced re-run, or the deferred re-run fires right after the backstop and races it. The `0`-disables convention keeps the switch in the one place operators already tune (the delay), avoiding a second enable/disable flag that can drift out of sync with the delay value.

## Examples

### Do This

```
const debouncer = createDebouncedTask(() => commitAndPush(workspace), minutes(5), log);

// on every event: reset the timer only — cheap, synchronous-ish
onExchange(() => debouncer.touch());

// the authoritative backstop takes over: cancel the timer, drain any in-flight run,
// then do the work itself — no deferred re-run fires afterwards
onClose(async () => {
  debouncer.clear();
  await debouncer.whenIdle();
  await commitAndPush(workspace);
});
```

**Why**: Each exchange only touches the timer; the expensive commit+push runs once, in the background, after 5 minutes of quiet. The close path clears the timer *and* the coalesced re-run flag, drains any in-flight run, then commits — so the two never race. `commitDebounceMinutes = 0` makes `touch()` a no-op, disabling mid-session persistence with no extra flag.

### Don't Do This

```
// drop touches that arrive during a run, and let a close race the deferred fire
const fire = () => { if (running) return; run(); };           // dropped touch = lost work until next event
onClose(() => { clearTimeout(timer); commitAndPush(); });     // a pending re-run still fires after close
```

**Why**: Dropping a mid-run touch means the work it represents waits for the *next* event to re-trigger — under sustained activity the job can stall behind one long run. And clearing only the timer leaves a coalesced re-run armed, so it fires immediately after the close path's own commit and races it.

## Exceptions

- A job that must run on *every* event (no batching) or at a *fixed* cadence (independent of events) is not a debounce — use direct invocation or a cron/scheduler job instead.
- A job whose touch should never be deferred past the first event (leading-edge) needs a different primitive; this pattern is strictly trailing-edge.

---

## Related

- Implementation: [`src/util/debouncer.ts`](../../src/util/debouncer.ts) — `createDebouncedTask`
- Related features: [../feature-designs/git-workspace.md](../feature-designs/git-workspace.md) and [../feature-designs/projects.md](../feature-designs/projects.md) — the debounced mid-session workspace and project commit-push
- See also: [DES-001](DES-001-unified-extension-api.md) — the debouncer is constructed inside extension `setup()` and driven through `app.sessions.onExchange` / `app.onShutdown`
