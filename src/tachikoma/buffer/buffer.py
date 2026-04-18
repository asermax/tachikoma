"""Priority buffer with event-driven loop for deferred delivery.

Owns a heapq-backed priority queue and a single async loop task that
wakes on enqueue, CoordinatorIdle events, or per-cycle timers.
"""

import asyncio
import contextlib
import heapq
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bubus import EventBus
from loguru import logger

from tachikoma.buffer.digest import build_shutdown_digest
from tachikoma.buffer.events import BufferedDelivery, CoordinatorIdle
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority
from tachikoma.config import BufferSettings, PriorityTiming
from tachikoma.notifications import Notification

if TYPE_CHECKING:
    from tachikoma.coordinator import Coordinator

_log = logger.bind(component="buffer")


class Buffer:
    """Priority buffer holding notifications and session tasks until delivery."""

    def __init__(
        self,
        bus: EventBus,
        coordinator: "Coordinator",
        settings: BufferSettings,
    ) -> None:
        self._bus = bus
        self._coordinator = coordinator
        self._settings = settings

        self._heap: list[tuple[int, int, BufferedItem]] = []
        self._arrival_counter: int = 0
        self._wake_event: asyncio.Event = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._current_front: BufferedItem | None = None
        self._shutdown_completion: asyncio.Future[None] | None = None

    async def start(self) -> None:
        """Start the buffer loop task."""
        self._loop_task = asyncio.create_task(self._loop())
        _log.info("Buffer started")

    async def stop(self) -> None:
        """Stop the buffer loop task."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
            _log.info("Buffer stopped")

    async def enqueue(self, item: BufferedItem) -> None:
        """Add an item to the priority buffer."""
        item.arrival_seq = self._arrival_counter
        self._arrival_counter += 1

        heapq.heappush(self._heap, (item.priority.value, item.arrival_seq, item))
        self._wake_event.set()

        _log.debug(
            "Enqueued: kind={kind}, priority={priority}, seq={seq}",
            kind=item.kind,
            priority=item.priority.name,
            seq=item.arrival_seq,
        )

    async def flush_on_shutdown(self) -> None:
        """Drain pending items as a single shutdown digest delivery."""
        try:
            items = sorted(
                [item for _, _, item in self._heap],
                key=lambda i: (i.priority.value, i.arrival_seq),
            )

            if not items:
                _log.info("Buffer empty at shutdown — no digest needed")
                return

            self._heap.clear()
            self._accumulate_front_time()
            self._current_front = None

            digest_prompt = build_shutdown_digest(items)

            self._shutdown_completion = asyncio.get_running_loop().create_future()

            _log.info("Flushing buffer as shutdown digest: items={count}", count=len(items))
            await self._bus.dispatch(
                BufferedDelivery(
                    prompt=digest_prompt,
                    items=items,
                    is_shutdown_digest=True,
                ),
            )

            # Wait for the channel to confirm delivery
            await self._shutdown_completion
            _log.info("Shutdown digest delivered successfully")
        except Exception:
            _log.exception("Error during shutdown flush")

    def resolve_shutdown(self) -> None:
        """Signal that the shutdown digest has been delivered."""
        if self._shutdown_completion is not None and not self._shutdown_completion.done():
            self._shutdown_completion.set_result(None)

    async def _handle_notification(self, event: Notification) -> None:
        """Subscribe handler: convert Notification to BufferedItem and enqueue."""
        item = BufferedItem.from_notification(event)
        await self.enqueue(item)

    async def _handle_coordinator_idle(self, event: CoordinatorIdle) -> None:
        """Subscribe handler: wake the loop on coordinator idle transitions."""
        self._wake_event.set()

    async def _loop(self) -> None:
        """Event-driven loop: evaluate front item eligibility on each wake."""
        while True:
            try:
                await self._wake_event.wait()
                self._wake_event.clear()

                if not self._heap:
                    continue

                while self._heap:
                    _, _, front_item = self._heap[0]

                    self._update_front_accounting(front_item)

                    if self._is_eligible(front_item):
                        heapq.heappop(self._heap)

                        self._accumulate_front_time()
                        self._current_front = None

                        await self._bus.dispatch(
                            BufferedDelivery(
                                prompt=front_item.prompt,
                                items=[front_item],
                            ),
                        )

                        _log.info(
                            "Delivered: kind={kind}, priority={priority}",
                            kind=front_item.kind,
                            priority=front_item.priority.name,
                        )

                        self._wake_event.clear()
                    else:
                        delta = self._compute_timer_delta(front_item)
                        if delta is None:
                            break

                        timer_task = asyncio.ensure_future(asyncio.sleep(delta))
                        wake_task = asyncio.ensure_future(self._wake_event.wait())

                        done, pending = await asyncio.wait(
                            [wake_task, timer_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        for task in pending:
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task

                        self._wake_event.clear()

            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("Buffer loop error; continuing")

    def _update_front_accounting(self, front_item: BufferedItem) -> None:
        """Track front-item transitions for preemption-aware time accounting."""
        if self._current_front is not front_item:
            if self._current_front is not None:
                self._accumulate_front_time()

            front_item.current_front_since = datetime.now(UTC)
            self._current_front = front_item

    def _accumulate_front_time(self) -> None:
        """Accumulate front time for the current front item."""
        if self._current_front is not None and self._current_front.current_front_since is not None:
            elapsed = (datetime.now(UTC) - self._current_front.current_front_since).total_seconds()
            self._current_front.total_front_time += elapsed
            self._current_front.current_front_since = None

    def _is_eligible(self, item: BufferedItem) -> bool:
        """Check if a front item is eligible for delivery."""
        if self._coordinator.is_busy:
            return False

        timing = self._timing_for(item.priority)

        now = datetime.now(UTC)

        last_msg = self._coordinator.last_message_time
        idle_satisfied = (
            last_msg is not None and (now - last_msg).total_seconds() >= timing.idle_window_seconds
        )

        front_time = item.total_front_time
        if item.current_front_since is not None:
            front_time += (now - item.current_front_since).total_seconds()

        max_hold_exceeded = (
            timing.max_hold_seconds is not None and front_time >= timing.max_hold_seconds
        )

        return idle_satisfied or max_hold_exceeded

    def _compute_timer_delta(self, item: BufferedItem) -> float | None:
        """Compute seconds until next actionable moment for the front item."""
        timing = self._timing_for(item.priority)
        now = datetime.now(UTC)

        deltas: list[float] = []

        last_msg = self._coordinator.last_message_time
        if last_msg is not None:
            idle_remaining = timing.idle_window_seconds - (now - last_msg).total_seconds()
            if idle_remaining > 0:
                deltas.append(idle_remaining)

        if timing.max_hold_seconds is not None and item.current_front_since is not None:
            front_time = item.total_front_time + (now - item.current_front_since).total_seconds()
            hold_remaining = timing.max_hold_seconds - front_time
            if hold_remaining > 0:
                deltas.append(hold_remaining)

        return min(deltas) if deltas else None

    def _timing_for(self, priority: Priority) -> PriorityTiming:
        """Get timing configuration for a priority level."""
        if priority == Priority.URGENT:
            return self._settings.urgent
        elif priority == Priority.NORMAL:
            return self._settings.normal
        else:
            return self._settings.low
