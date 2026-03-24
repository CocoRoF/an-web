"""Unit tests for EventLoopScheduler."""
from __future__ import annotations

import asyncio
import pytest
from an_web.core.scheduler import EventLoopScheduler


class TestEventLoopScheduler:
    @pytest.fixture
    def scheduler(self):
        return EventLoopScheduler()

    async def test_drain_microtasks_empty(self, scheduler):
        await scheduler.drain_microtasks()  # should not raise

    async def test_queue_and_drain_microtask(self, scheduler):
        results = []

        async def task():
            results.append(1)

        scheduler.queue_microtask(task)
        await scheduler.drain_microtasks()
        assert results == [1]

    async def test_microtasks_queued_during_drain(self, scheduler):
        """Microtasks added during drain are also processed."""
        results = []

        async def task_b():
            results.append("b")

        async def task_a():
            results.append("a")
            scheduler.queue_microtask(task_b)

        scheduler.queue_microtask(task_a)
        await scheduler.drain_microtasks()
        assert results == ["a", "b"]

    async def test_set_timeout_returns_id(self, scheduler):
        async def noop():
            pass
        tid = scheduler.set_timeout(noop, delay_ms=100)
        assert isinstance(tid, int)
        assert tid > 0

    async def test_clear_timeout_removes_timer(self, scheduler):
        results = []

        async def task():
            results.append(1)

        tid = scheduler.set_timeout(task, delay_ms=0)
        scheduler.clear_timeout(tid)
        await scheduler.run_macrotasks(max_wait_ms=50)
        assert results == []

    async def test_run_transaction_calls_drain(self, scheduler):
        drained = []

        async def task():
            async def microtask():
                drained.append(1)
            scheduler.queue_microtask(microtask)
            return "done"

        result = await scheduler.run_transaction(task())
        assert result == "done"
        assert drained == [1]

    async def test_settle_network_no_callbacks(self, scheduler):
        # Should complete immediately with no callbacks registered
        await asyncio.wait_for(scheduler.settle_network(timeout=1.0), timeout=2.0)

    async def test_flush_dom_mutations_no_callbacks(self, scheduler):
        await scheduler.flush_dom_mutations()  # should not raise

    async def test_flush_dom_mutations_calls_callbacks(self, scheduler):
        called = []

        async def mutation_flush():
            called.append(True)

        scheduler.register_mutation_flush(mutation_flush)
        await scheduler.flush_dom_mutations()
        assert called == [True]
