"""
asyncio-based event loop scheduler for AN-Web.

Lifecycle::

    scheduler = EventLoopScheduler()

    # Register timer from JS host API
    timer_id = scheduler.set_timeout(my_callback, delay_ms=100)
    scheduler.clear_timeout(timer_id)

    # Recurring timer (setInterval equivalent)
    interval_id = scheduler.set_interval(my_callback, interval_ms=500)
    scheduler.clear_interval(interval_id)

    # Microtask (queueMicrotask / Promise job)
    scheduler.queue_microtask(async_fn)
    await scheduler.drain_microtasks()

    # Full action transaction
    result = await scheduler.run_transaction(my_action_coro)
"""
from __future__ import annotations

import asyncio
import heapq
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Safety caps
_MAX_MICROTASK_ITERATIONS = 10_000
_MAX_MACROTASK_ITERATIONS = 1_000


@dataclass(order=True)
class _TimerEntry:
    """Heap entry for a macrotask timer."""

    fire_at: float       # monotonic clock target
    timer_id: int        # unique id for cancellation
    interval_ms: int = field(compare=False, default=0)   # 0 = one-shot; >0 = recurring
    callback: Callable[[], Awaitable[Any]] = field(compare=False, default=None)  # type: ignore[assignment]
    cancelled: bool = field(compare=False, default=False)


class EventLoopScheduler:
    """
    Manages micro/macro task queues and timers for a browser session.

    Design: each AI action runs as a transaction::

        precondition -> execute -> drain_microtasks -> settle_network
                    -> flush_dom_mutations -> postcondition

    Thread-safety: NOT thread-safe. Use one scheduler per async session.
    """

    def __init__(self) -> None:
        # Microtask queue (Promise callbacks, queueMicrotask)
        self._microtask_queue: list[Callable[[], Awaitable[Any]]] = []

        # Macrotask heap (setTimeout / setInterval) -- heapq min-heap by fire_at
        self._macrotask_heap: list[_TimerEntry] = []

        # Set of timer IDs that have been cancelled (for in-flight callbacks)
        self._cancelled_ids: set[int] = set()

        # Monotonically increasing timer counter
        self._timer_counter: int = 0

        # Registered callbacks for network settle phase
        self._network_settle_callbacks: list[Callable[[], Awaitable[Any]]] = []

        # Registered callbacks for DOM mutation flush phase
        self._mutation_flush_callbacks: list[Callable[[], Awaitable[Any]]] = []

    # ------------------------------------------------------------------
    # Transaction API (primary entry point for AI actions)
    # ------------------------------------------------------------------

    async def run_transaction(
        self,
        action_coro: Awaitable[Any],
        network_timeout: float = 5.0,
    ) -> Any:
        """
        Execute an action as an atomic transaction with full event loop drain.

        Args:
            action_coro:     The action coroutine to await.
            network_timeout: Seconds to wait for network settle (non-fatal).

        Returns:
            The return value of action_coro.
        """
        result = await action_coro
        await self.drain_microtasks()
        await self.settle_network(timeout=network_timeout)
        await self.flush_dom_mutations()
        return result

    # ------------------------------------------------------------------
    # Microtask queue
    # ------------------------------------------------------------------

    def queue_microtask(self, callback: Callable[[], Awaitable[Any]]) -> None:
        """
        Enqueue a microtask (Promise job / queueMicrotask equivalent).

        The callback must be a zero-argument async callable.
        Microtasks are processed FIFO before any macrotask fires.
        """
        self._microtask_queue.append(callback)

    async def drain_microtasks(self) -> int:
        """
        Drain the microtask queue until empty.

        Microtasks enqueued *during* draining are also processed (reentrancy),
        mirroring the HTML spec's "perform a microtask checkpoint" algorithm.

        Returns:
            Total number of microtask callbacks executed.
        """
        total = 0
        while self._microtask_queue and total < _MAX_MICROTASK_ITERATIONS:
            # Snapshot and clear so that callbacks added during this round
            # are picked up in the next iteration.
            batch = self._microtask_queue[:]
            self._microtask_queue.clear()
            for task in batch:
                try:
                    await task()
                except Exception as exc:
                    log.debug("microtask raised: %s", exc)
                total += 1
            # Yield to asyncio between batches so network I/O can proceed
            await asyncio.sleep(0)

        if total >= _MAX_MICROTASK_ITERATIONS:
            log.warning(
                "drain_microtasks hit safety cap (%d) — possible infinite loop",
                _MAX_MICROTASK_ITERATIONS,
            )
        return total

    # ------------------------------------------------------------------
    # Macrotask / timer wheel
    # ------------------------------------------------------------------

    @property
    def timer_wheel(self) -> list[_TimerEntry]:
        """Read-only view of pending macrotask timers (heapq order)."""
        return self._macrotask_heap

    @property
    def pending_timer_count(self) -> int:
        """Number of non-cancelled timers currently scheduled."""
        return sum(1 for e in self._macrotask_heap if not e.cancelled)

    def set_timeout(
        self,
        callback: Callable[[], Awaitable[Any]],
        delay_ms: int = 0,
    ) -> int:
        """
        Register a one-shot macrotask timer.

        Args:
            callback: Async callable to invoke when the timer fires.
            delay_ms: Minimum delay in milliseconds (>= 0).

        Returns:
            timer_id that can be passed to clear_timeout().
        """
        self._timer_counter += 1
        timer_id = self._timer_counter
        fire_at = time.monotonic() + max(0, delay_ms) / 1000.0
        entry = _TimerEntry(
            fire_at=fire_at,
            timer_id=timer_id,
            interval_ms=0,
            callback=callback,
        )
        heapq.heappush(self._macrotask_heap, entry)
        return timer_id

    def set_interval(
        self,
        callback: Callable[[], Awaitable[Any]],
        interval_ms: int,
    ) -> int:
        """
        Register a recurring macrotask timer (setInterval equivalent).

        The callback fires every *interval_ms* milliseconds until
        clear_interval() is called.

        Args:
            callback:    Async callable to invoke on each interval.
            interval_ms: Repeat interval in milliseconds (>= 1).

        Returns:
            timer_id that can be passed to clear_interval().
        """
        interval_ms = max(1, interval_ms)
        self._timer_counter += 1
        timer_id = self._timer_counter
        fire_at = time.monotonic() + interval_ms / 1000.0
        entry = _TimerEntry(
            fire_at=fire_at,
            timer_id=timer_id,
            interval_ms=interval_ms,
            callback=callback,
        )
        heapq.heappush(self._macrotask_heap, entry)
        return timer_id

    def clear_timeout(self, timer_id: int) -> None:
        """
        Cancel a pending one-shot timer.

        Cancelled entries are lazily removed when run_macrotasks() fires them.
        Cancelling a non-existent or already-fired timer is a no-op.
        Also handles cancellation from *within* a running callback via
        the _cancelled_ids set.
        """
        self._cancelled_ids.add(timer_id)
        for entry in self._macrotask_heap:
            if entry.timer_id == timer_id:
                entry.cancelled = True
                return

    def clear_interval(self, timer_id: int) -> None:
        """Cancel a recurring interval timer. Alias of clear_timeout()."""
        self.clear_timeout(timer_id)

    async def run_macrotasks(self, max_wait_ms: int = 100) -> int:
        """
        Fire all macrotasks whose delay has elapsed within max_wait_ms.

        After each macrotask fires, drain_microtasks() is called so that
        Promise continuations are processed before the next timer.

        Args:
            max_wait_ms: Look-ahead window in milliseconds.

        Returns:
            Number of macrotask callbacks fired.
        """
        deadline = time.monotonic() + max_wait_ms / 1000.0
        fired = 0

        while self._macrotask_heap and fired < _MAX_MACROTASK_ITERATIONS:
            entry = self._macrotask_heap[0]
            if entry.fire_at > deadline:
                break

            heapq.heappop(self._macrotask_heap)

            if entry.cancelled:
                continue

            try:
                await entry.callback()
            except Exception as exc:
                log.debug("macrotask timer_id=%d raised: %s", entry.timer_id, exc)

            fired += 1

            # Re-queue recurring timers (also check _cancelled_ids for in-callback cancels)
            if entry.interval_ms > 0 and not entry.cancelled and entry.timer_id not in self._cancelled_ids:
                next_entry = _TimerEntry(
                    fire_at=entry.fire_at + entry.interval_ms / 1000.0,
                    timer_id=entry.timer_id,
                    interval_ms=entry.interval_ms,
                    callback=entry.callback,
                )
                heapq.heappush(self._macrotask_heap, next_entry)

            # Each macrotask may enqueue microtasks (Promise handlers)
            await self.drain_microtasks()

        return fired

    # ------------------------------------------------------------------
    # Network settle phase
    # ------------------------------------------------------------------

    def register_network_settle(
        self,
        callback: Callable[[], Awaitable[Any]],
    ) -> None:
        """
        Register an async callback to be awaited during settle_network().

        Typically called by the Session's HTTP client to expose a
        "wait until all in-flight requests are done" hook.
        """
        self._network_settle_callbacks.append(callback)

    async def settle_network(self, timeout: float = 5.0) -> None:
        """
        Wait for all registered network settle callbacks to complete.

        Timeout is non-fatal — a partial settle is logged but not raised.

        Args:
            timeout: Maximum seconds to wait (default 5.0).
        """
        if not self._network_settle_callbacks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(cb() for cb in self._network_settle_callbacks),
                    return_exceptions=True,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            log.debug("settle_network timed out after %.1fs", timeout)

    # ------------------------------------------------------------------
    # DOM mutation flush phase
    # ------------------------------------------------------------------

    def register_mutation_flush(
        self,
        callback: Callable[[], Awaitable[Any]],
    ) -> None:
        """
        Register an async callback to be called during flush_dom_mutations().

        Typically used by MutationObserver or custom DOM-change listeners.
        """
        self._mutation_flush_callbacks.append(callback)

    async def flush_dom_mutations(self) -> int:
        """
        Invoke all registered MutationObserver / DOM-flush callbacks.

        After flushing, drain_microtasks() is called once because
        observer callbacks may enqueue Promise continuations.

        Returns:
            Number of mutation-flush callbacks invoked.
        """
        count = len(self._mutation_flush_callbacks)
        for cb in self._mutation_flush_callbacks:
            try:
                await cb()
            except Exception as exc:
                log.debug("mutation flush callback raised: %s", exc)
        if count:
            await self.drain_microtasks()
        return count

    # ------------------------------------------------------------------
    # Lifecycle / introspection
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Clear all queues and registered callbacks.

        Called by Session.navigate() to discard timers/callbacks from
        the previous page before loading a new one.
        """
        self._microtask_queue.clear()
        self._macrotask_heap.clear()
        self._cancelled_ids.clear()
        self._network_settle_callbacks.clear()
        self._mutation_flush_callbacks.clear()
        # Note: _timer_counter is intentionally NOT reset so timer IDs
        # remain unique across navigations within a session.

    def stats(self) -> dict[str, int]:
        """Return diagnostic counters."""
        return {
            "microtask_queue_len": len(self._microtask_queue),
            "pending_timers": self.pending_timer_count,
            "total_heap_entries": len(self._macrotask_heap),
            "network_settle_callbacks": len(self._network_settle_callbacks),
            "mutation_flush_callbacks": len(self._mutation_flush_callbacks),
            "timer_counter": self._timer_counter,
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"EventLoopScheduler("
            f"microtasks={s['microtask_queue_len']}, "
            f"timers={s['pending_timers']}, "
            f"net_callbacks={s['network_settle_callbacks']})"
        )
