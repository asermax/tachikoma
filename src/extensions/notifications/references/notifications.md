# Notifications

How anything running outside the conversation talks to it. Owned by the notifications
extension.

## Delivery

Every producer emits a `"notify"` event with a severity; the router maps severity to a
delivery tier and hands it to the conversation's delivery queue, which owns *when* it lands —
each tier waits for idle and is forced out after a tier-specific max hold (exact windows in
the core conversation reference; urgent arrives near-immediately, low never forces a turn).
Severity → tier is fixed: `urgent`→Urgent, `warning`→Normal, `info`→Low.

Several accumulated items are batched into one digest turn (`<queued-notifications>`,
ordered urgent → low, FIFO within a tier). The producer picks the severity; you cannot
change it.

## Sources

Detached-process exits, upgrade notices, skill-evolution reports, background-task failures
and asks — anything can emit, from any extension. Each notice is self-contained text; a
notice reads as an update, an instruction as a request.

## Emitting from a background run

Background sessions have the `notify_user` tool for exactly this: whether to surface an
outcome is the run's call, guided by its task instructions — self-contained summaries for
what deserves attention, silence for routine no-ops. Failure notices are sent automatically.
In the main conversation there is deliberately no such tool: your reply *is* the channel.

## Configuration

`[extensions.notifications]`: `flushWindowSeconds` (default `30`) — how long the router
waits to batch same-tick notices before delivering; `dedupTtlSeconds` (default `60`) —
window in which an identical (source + text) notice is dropped, guarding against re-emit
storms.
