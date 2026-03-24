"""
asyncio-based event loop scheduler for AN-Web.

Mirrors Lightpanda's Browser.zig event loop:
    runMicrotasks()   → drain_microtasks()
    runMacrotasks()   → run_macrotasks()
    pumpMessageLoop() → settle_network()
"""
from __future__ import annotations

import asyncio
import heapq
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class _TimerEntry:
    fire_at: float
    timer_id: int
    callback: Callable[[], Awaitable[Any]] = field(compare=False)


class EventLoopScheduler:
    """
    Manages micro/macro task queues and timers for a browser session.

    Design: each AI action runs as a transaction:
        precondition → execute → drain_microtasks → settle_network
                    → flush_dom_mutations → postcondition

    This matches Lightpanda's deterministic action boundary semantics.
    """

    def __init__(self) -> None:
        self._microtask_queue: list[Callable[[], Awaitable[Any]]] = []
        self._macrotask_heap: list[_TimerEntry] = []
        self._timer_counter = 0
        self._network_settle_callbacks: list[Callable[[], Awaitable[Any]]] = []
        self._mutation_flush_callbacks: list[Callable[[], Awaitable[Any]]] = []

    async def run_transaction(
        self,
        action_coro: Awaitable[Any],
        network_timeout: float = 5.0,
    ) -> Any:
        """
        Execute an action as an atomic transaction with full event loop drain.

        Pattern from Lightpanda actions.zig:
            execute → event_flush → postcondition → artifact_collection
        """
        result = await action_coro
        await self.drain_microtasks()
        await self.settle_network(timeout=network_timeout)
        await self.flush_dom_mutations()
        return result

    def queue_microtask(self, callback: Callable[[], Awaitable[Any]]) -> None:
        """Enqueue a microtask (Promise job equivalent)."""
        self._microtask_queue.append(callback)

    async def drain_microtasks(self) -> None:
        """
        Drain the microtask queue until empty.
        Microtasks enqueued during draining are also processed (reentrancy).
        """
        iterations = 0
        max_iterations = 10_000  # guard against infinite loops

        while self._microtask_queue and iterations < max_iterations:
            tasks = self._microtask_queue[:]
            self._microtask_queue.clear()
            for task in tasks:
                await task()
            iterations += len(tasks)

    def set_timeout(
        self,
        callback: Callable[[], Awaitable[Any]],
        delay_ms: int = 0,
    ) -> int:
        """Register a macrotask timer. Returns timer_id."""
        self._timer_counter += 1
        timer_id = self._timer_counter
        fire_at = time.monotonic() + delay_ms / 1000.0
        entry = _TimerEntry(fire_at=fire_at, timer_id=timer_id, callback=callback)
        heapq.heappush(self._macrotask_heap, entry)
        return timer_id

    def clear_timeout(self, timer_id: int) -> None:
        """Cancel a pending timer."""
        self._macrotask_heap = [
            e for e in self._macrotask_heap if e.timer_id != timer_id
        ]
        heapq.heapify(self._macrotask_heap)

    async def run_macrotasks(self, max_wait_ms: int = 100) -> None:
        """Fire all macrotasks whose delay has elapsed."""
        deadline = time.monotonic() + max_wait_ms / 1000.0
        while self._macrotask_heap:
            entry = self._macrotask_heap[0]
            if entry.fire_at > deadline:
                break
            heapq.heappop(self._macrotask_heap)
            await entry.callback()
            await self.drain_microtasks()  # each macrotask may queue microtasks

    def register_network_settle(
        self, callback: Callable[[], Awaitable[Any]]
    ) -> None:
        self._network_settle_callbacks.append(callback)

    async def settle_network(self, timeout: float = 5.0) -> None:
        """Wait for pending network requests to complete."""
        if not self._network_settle_callbacks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*[cb() for cb in self._network_settle_callbacks]),
                timeout=timeout,
            )
        except TimeoutError:
            pass  # network settle timeout is non-fatal

    def register_mutation_flush(
        self, callback: Callable[[], Awaitable[Any]]
    ) -> None:
        self._mutation_flush_callbacks.append(callback)

    async def flush_dom_mutations(self) -> None:
        """Trigger MutationObserver callbacks."""
        for cb in self._mutation_flush_callbacks:
            await cb()
