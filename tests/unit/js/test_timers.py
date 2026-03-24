"""Unit tests for js/timers.py — TimerManager."""
from __future__ import annotations

import asyncio
import pytest

from an_web.core.scheduler import EventLoopScheduler
from an_web.js.timers import TimerManager


@pytest.fixture
def scheduler():
    return EventLoopScheduler()


@pytest.fixture
def timer_mgr(scheduler):
    return TimerManager(scheduler)


class TestTimerManagerSetTimeout:
    @pytest.mark.asyncio
    async def test_set_timeout_returns_int_id(self, timer_mgr):
        called = []
        tid = timer_mgr.set_timeout(lambda: called.append(1), 0)
        assert isinstance(tid, int)
        assert tid >= 0

    @pytest.mark.asyncio
    async def test_set_timeout_fires_callback(self, timer_mgr, scheduler):
        called = []
        timer_mgr.set_timeout(lambda: called.append("fired"), 0)
        await scheduler.run_macrotasks()
        assert called == ["fired"]

    @pytest.mark.asyncio
    async def test_set_timeout_default_delay_zero(self, timer_mgr, scheduler):
        called = []
        timer_mgr.set_timeout(lambda: called.append(1))  # default delay_ms=0
        await scheduler.run_macrotasks()
        assert called == [1]

    @pytest.mark.asyncio
    async def test_multiple_timeouts_fire_in_order(self, timer_mgr, scheduler):
        order = []
        timer_mgr.set_timeout(lambda: order.append(1), 0)
        timer_mgr.set_timeout(lambda: order.append(2), 0)
        timer_mgr.set_timeout(lambda: order.append(3), 0)
        await scheduler.run_macrotasks()
        assert order == [1, 2, 3]


class TestTimerManagerClearTimeout:
    @pytest.mark.asyncio
    async def test_clear_timeout_prevents_fire(self, timer_mgr, scheduler):
        called = []
        tid = timer_mgr.set_timeout(lambda: called.append("should not fire"), 0)
        timer_mgr.clear_timeout(tid)
        await scheduler.run_macrotasks()
        assert called == []

    @pytest.mark.asyncio
    async def test_clear_timeout_invalid_id_does_not_raise(self, timer_mgr):
        timer_mgr.clear_timeout(99999)  # should be a no-op

    @pytest.mark.asyncio
    async def test_clear_one_leaves_others(self, timer_mgr, scheduler):
        called = []
        t1 = timer_mgr.set_timeout(lambda: called.append(1), 0)
        timer_mgr.set_timeout(lambda: called.append(2), 0)
        timer_mgr.clear_timeout(t1)
        await scheduler.run_macrotasks()
        assert called == [2]


class TestTimerManagerQueueMicrotask:
    @pytest.mark.asyncio
    async def test_queue_microtask_fires(self, timer_mgr, scheduler):
        called = []
        timer_mgr.queue_microtask(lambda: called.append("micro"))
        await scheduler.drain_microtasks()
        assert called == ["micro"]

    @pytest.mark.asyncio
    async def test_queue_multiple_microtasks(self, timer_mgr, scheduler):
        order = []
        timer_mgr.queue_microtask(lambda: order.append(1))
        timer_mgr.queue_microtask(lambda: order.append(2))
        timer_mgr.queue_microtask(lambda: order.append(3))
        await scheduler.drain_microtasks()
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_microtask_fires_before_macrotask(self, timer_mgr, scheduler):
        order = []
        timer_mgr.set_timeout(lambda: order.append("macro"), 0)
        timer_mgr.queue_microtask(lambda: order.append("micro"))
        await scheduler.drain_microtasks()
        await scheduler.run_macrotasks()
        # micro should have fired first (already drained before macrotasks ran)
        assert order[0] == "micro"
        assert "macro" in order


class TestTimerManagerIntegration:
    @pytest.mark.asyncio
    async def test_scheduler_reference_preserved(self, timer_mgr, scheduler):
        assert timer_mgr._scheduler is scheduler

    @pytest.mark.asyncio
    async def test_ids_are_unique(self, timer_mgr):
        ids = {timer_mgr.set_timeout(lambda: None, 0) for _ in range(10)}
        assert len(ids) == 10  # all unique
