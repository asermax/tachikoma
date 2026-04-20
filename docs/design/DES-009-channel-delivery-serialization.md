# DES-009: Channel Delivery Serialization via asyncio.Lock

**Scope**: Python / Channels
**Date**: 2026-04-19
**Last Updated**: 2026-04-20 (steering branch via `Coordinator.in_exchange`)
**First Used**: DLT-112 (REPL), DLT-111 (Telegram)

## Problem

Channels (REPL, Telegram, future) have multiple concurrent entry points that route a message into the coordinator — user input handlers, buffered-delivery event handlers, media handlers. Each entry point calls `coordinator.enqueue(text)` followed by `_process_through_coordinator()`, which iterates `coordinator.send_message()` until `Result`.

Coordinator teardown after `Result` is not instantaneous: the SDK client still needs to be disconnected, per-turn queues drained back, and internal state cleared. A naive boolean flag (`_is_processing = True/False` around the processing call) leaves a window between clearing the flag and finishing teardown where a concurrent entry point can observe the channel as idle, call `enqueue`, and call `send_message()` while the previous exchange's teardown is still in flight. This can race the coordinator's internal state transitions and — combined with the single-queue coordinator design — drop messages.

Even after migrating the coordinator to two-queue isolation (so drain-back cannot lose messages), the race remains undesirable: two entry points overlapping means two in-flight coordinator exchanges, renderer state collisions, and duplicated UI output.

## Solution

Each channel owns `self._delivery_lock: asyncio.Lock`. Every entry point that delivers a message to the coordinator wraps `enqueue(...)` plus the `_process_through_coordinator(...)` call in `async with self._delivery_lock:`. Follow-up entries that arrive while a delivery is in flight queue on the lock (CPython `asyncio.Lock` is FIFO in 3.10+) and become the next acquisition after the in-flight exchange has fully torn down.

Include the coordinator's teardown inside the critical section by keeping `_process_through_coordinator()` as the lock-holder — the lock is released only after `send_message()` has returned, i.e. after the coordinator has finished disconnecting the SDK client, draining back per-turn queues, and clearing `_client`.

### When to Use

- Channel has **multiple concurrent entry points** that each deliver to the coordinator (user input, buffered delivery, media, reactive events).
- Each entry point does `enqueue + _process_through_coordinator` as a pair.
- The coordinator's per-exchange setup/teardown must not overlap with the next exchange.

### When NOT to Use

- If you want a second entry point to *steer* the current exchange (push a mid-stream message into the still-active SDK session), do not put that path under the same lock — use the coordinator's `enqueue()` alone (the `_forwarder` will move it onto the live `_sdk_inbox`). The lock serializes distinct *exchanges*, not items within one. Gate this with `coordinator.in_exchange` so the entry point only takes the steering branch when an SDK client is actually connected; otherwise fall back to the lock-based new-exchange path.
- Channels with a single entry point don't need this pattern.

## Example

### REPL

```python
class Repl:
    def __init__(self) -> None:
        self._delivery_lock: asyncio.Lock = asyncio.Lock()
        self._delivery_tasks: set[asyncio.Task] = set()

    async def _handle_buffered_delivery(self, event: BufferedDelivery) -> None:
        # Non-blocking: spawn task so the EventBus is freed immediately
        task = asyncio.create_task(self._deliver(event))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _deliver(self, event: BufferedDelivery) -> None:
        try:
            async with self._delivery_lock:
                await self._execute_buffered_delivery(event)
        except Exception:
            _log.exception("Error in detached delivery task")
        finally:
            if event.is_shutdown_digest and self._buffer is not None:
                self._buffer.resolve_shutdown()

    async def _handle_user_input(self, text: str) -> None:
        async with self._delivery_lock:
            self._coordinator.enqueue(text)
            await self._process_through_coordinator()
```

### Telegram

```python
class TelegramChannel:
    def __init__(self) -> None:
        self._delivery_lock: asyncio.Lock = asyncio.Lock()
        self._delivery_tasks: set[asyncio.Task] = set()

    async def _handle_message(self, message: Message) -> None:
        text = message.text.strip()

        # Mid-response: enqueue-only so the coordinator's forwarder steers
        # the live SDK exchange. Skipping the lock is required — taking it
        # would queue the message as a new turn after the response ends.
        if self._coordinator.in_exchange:
            self._coordinator.enqueue(text)
            return

        async with self._delivery_lock:
            self._coordinator.enqueue(text)
            await self._process_through_coordinator()

    async def _handle_media(self, message: Message) -> None:
        # ...download and build description first (outside the lock)...
        if self._coordinator.in_exchange:
            self._coordinator.enqueue(description)
            return

        async with self._delivery_lock:
            self._coordinator.enqueue(description)
            await self._process_through_coordinator()

    async def _handle_buffered_delivery(self, event: BufferedDelivery) -> None:
        # Non-blocking: spawn task so the EventBus is freed immediately
        task = asyncio.create_task(self._deliver(event))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _deliver(self, event: BufferedDelivery) -> None:
        try:
            async with self._delivery_lock:
                self._coordinator.enqueue(event.prompt)
                await self._process_through_coordinator(on_complete=self._build_on_complete(event))
        except Exception:
            _log.exception("Error in detached delivery task")
        finally:
            if event.is_shutdown_digest and self._buffer is not None:
                self._buffer.resolve_shutdown()
```

## Do / Don't

**Do**
- Acquire the lock *before* `enqueue` so the caller cannot race a concurrent `send_message()`.
- Keep the `_process_through_coordinator()` call inside the critical section so teardown is serialized with the next acquisition.
- Do long-running work that is not delivery-related (e.g. downloading a media file) *outside* the lock.
- Spawn a detached task for bus-dispatched handlers (`_handle_buffered_delivery`) so the EventBus is freed immediately. Store task references in a `set[asyncio.Task]` with `add_done_callback(discard)` cleanup.

**Don't**
- Don't clear a boolean flag around the delivery call — transitions and teardown are not atomic, leaving a race window.
- Don't try to push mid-stream steering messages under this lock — they should go through the coordinator's queue without blocking on the lock. Use `coordinator.in_exchange` as the gate so user-input handlers steer mid-response and only acquire the lock when starting a fresh exchange.
- Don't hold the lock while awaiting user-visible I/O that can block indefinitely (e.g. external network calls unrelated to the exchange).

## Consequences

- **Pro**: Coordinator teardown is guaranteed to complete before the next exchange begins — no state overlap between exchanges.
- **Pro**: FIFO fairness from `asyncio.Lock` (CPython 3.10+) preserves user-visible ordering of entry points.
- **Pro**: Uniform discipline across channels — reviewers can audit the same property everywhere.
- **Con**: Strict serialization means even concurrent entry points that would be safe to interleave do not. Acceptable because these paths are user-driven and infrequent.
- **Con**: Care needed when extracting helpers — any method that performs the delivery pair must participate in the lock (or be called only from within one).
