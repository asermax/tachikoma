# DES-008: Turn-Scoped Anchored-Prefix Header Recomposed by the Streaming Renderer

**Scope**: Project-wide (channels)
**Date**: 2026-06-21
**Last Updated**: 2026-06-21

## Pattern

When a channel renders an agent exchange by sending one message and **editing it in place** as text streams (the progressive-renderer model), and a per-exchange descriptor — a decision label, a status, a breadcrumb — must stay visible *while* the body streams but must **not** persist beyond the exchange, render it as an **anchored prefix** the renderer recomposes on every edit, and **drop it after the exchange**.

The defining constraint: an in-place edit (`editMessageText` and its analogs) replaces the **full** message text — there is no append/patch. So a prefix cannot be sent once and left; the renderer must rebuild `header + body (+ transient)` on every flush. "Persistent" here means *resistant to the streaming renderer within one turn*, not sticky across turns — the header is read fresh from the exchange's metadata, anchored before the first chunk streams, and never carried to a later exchange.

Two correctness details recur:

- **Turn-scoped by construction.** The header is a field on the per-exchange renderer, set from that exchange's descriptor, not a channel-global banner. The next exchange starts with no header unless its own metadata carries one.
- **Best-effort under length limits.** The full composition is bounded by the platform's message-length limit. When the body grows past it, degrade in priority order — drop the transient live line first, then the header itself (and log it) — because the streamed body is the point of the turn. The descriptor already took effect regardless of whether its header renders.

## Rationale

A streamed response and a decision that produced it (an automatic topic shift, a checkpoint, a rollback) are one logical event, so the user should see the decision on the same message as the response, at the moment it happens — but a lingering banner across later turns is noise. Editing in place is what makes streaming feel responsive on platforms whose edits replace the whole text, so the renderer already recomposes the body on every edit; anchoring the header as a prefix reuses that recomposition rather than opening a second message (which would linger and break the single-message stream). Reading the descriptor from per-exchange metadata keeps the header turn-scoped without any cross-turn state, and dropping it on overflow honors the priority of the response text while still having recorded the decision in state.

## Examples

### Do This

```
// per-exchange renderer; header is a field, set from this exchange's descriptor
renderer.setHeader({ label, note })          // before the first chunk streams
// every edit recomposes the full text — the prefix survives streaming
display = header ? `${header}\n\n${body}` : body
// turn-scoped: the renderer is discarded with the exchange; no cross-turn carry
// best-effort: if header + body exceeds the limit, drop transient then header (log it)
```

**Why**: The header rides the same in-place edits the body does, so it survives streaming without a second message. Because it is a field on the per-exchange renderer read from that exchange's metadata, it cannot leak into the next turn. Degradation drops the lowest-priority content first, keeping the response text intact.

### Don't Do This

```
// send the header as a separate message before the response
await send(`**${label}** — ${note}`)          // lingers in the chat past the exchange
await streamResponse(...)                      // a second message, forever separate

// or carry a channel-global "last header" across turns
lastHeader = descriptor                        // survives into the next exchange — a stale banner
```

**Why**: A separate header message lingers after the exchange and detaches the decision from its response. A channel-global header leaks across turns, surfacing a stale decision on later exchanges that made none.

## Exceptions

- A descriptor that *should* persist across turns (a durable status, a mode indicator) is not this pattern — it wants a dedicated, explicitly-managed surface, not a turn-scoped prefix.
- A channel that never edits in place (append-only output) has no recomposition to reuse; it can render the header inline with the first send, but the turn-scoping discipline still applies.

---

## Related

- See also: [DES-004](DES-004-logging-conventions.md) — user-facing progress goes through `app.status()`; this pattern is for a decision descriptor that must co-stream with a response, a distinct concern from a transient status line
- Related feature: [../feature-designs/telegram.md](../feature-designs/telegram.md) — `StreamRenderer.setHeader`/`compose`/`finalize` (the first implementation; drops the header best-effort past the 4096 edit limit)
- Related feature: [../feature-designs/conversation-loop.md](../feature-designs/conversation-loop.md) — the coordinator forwards the turn-scoped `decisionHeader` descriptor to `channel.respond({ header })`
- Future: the REPL channel (DLT-178) reuses this mechanism when it adopts progressive rendering
