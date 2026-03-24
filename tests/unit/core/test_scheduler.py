"""Unit tests for an_web/core/scheduler.py -- EventLoopScheduler."""
from __future__ import annotations

import asyncio
import time
import pytest
from an_web.core.scheduler import EventLoopScheduler, _TimerEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sched():
    return EventLoopScheduler()


# ===========================================================================
# _TimerEntry ordering
# ===========================================================================


class TestTimerEntry:
    def test_order_by_fire_at(self):
        import heapq
        a = _TimerEntry(fire_at=1.0, timer_id=1, callback=None)
        b = _TimerEntry(fire_at=0.5, timer_id=2, callback=None)
        heap = [a, b]
        heapq.heapify(heap)
        assert heapq.heappop(heap).fire_at == 0.5

    def test_order_by_timer_id_when_equal_fire_at(self):
        import heapq
        a = _TimerEntry(fire_at=1.0, timer_id=2, callback=None)
        b = _TimerEntry(fire_at=1.0, timer_id=1, callback=None)
        heap = [a, b]
        heapq.heapify(heap)
        assert heapq.heappop(heap).timer_id == 1

    def test_default_not_cancelled(self):
        e = _TimerEntry(fire_at=0.0, timer_id=1, callback=None)
        assert e.cancelled is False

    def test_default_one_shot(self):
        e = _TimerEntry(fire_at=0.0, timer_id=1, callback=None)
        assert e.interval_ms == 0


# ===========================================================================
# Initialisation / repr / stats
# ===========================================================================


class TestInit:
    def test_fresh_scheduler_stats(self, sched):
        s = sched.stats()
        assert s["microtask_queue_len"] == 0
        assert s["pending_timers"] == 0
        assert s["total_heap_entries"] == 0
        assert s["network_settle_callbacks"] == 0
        assert s["mutation_flush_callbacks"] == 0
        assert s["timer_counter"] == 0

    def test_repr_contains_info(self, sched):
        r = repr(sched)
        assert "EventLoopScheduler" in r
        assert "microtasks=0" in r

    def test_timer_wheel_property_empty(self, sched):
        assert sched.timer_wheel == []

    def test_pending_timer_count_empty(self, sched):
        assert sched.pending_timer_count == 0


# ===========================================================================
# Microtask queue
# ===========================================================================


class TestMicrotasks:
    async def test_drain_empty_returns_zero(self, sched):
        count = await sched.drain_microtasks()
        assert count == 0

    async def test_single_microtask_runs(self, sched):
        results = []

        async def task():
            results.append(1)

        sched.queue_microtask(task)
        count = await sched.drain_microtasks()
        assert results == [1]
        assert count == 1

    async def test_multiple_microtasks_fifo(self, sched):
        order = []

        async def make_task(n):
            async def task():
                order.append(n)
            return task

        for i in range(5):
            sched.queue_microtask(await make_task(i))

        await sched.drain_microtasks()
        assert order == [0, 1, 2, 3, 4]

    async def test_microtasks_queued_during_drain_are_processed(self, sched):
        """Microtasks enqueued inside a callback are also drained."""
        results = []

        async def task_b():
            results.append("b")

        async def task_a():
            results.append("a")
            sched.queue_microtask(task_b)

        sched.queue_microtask(task_a)
        await sched.drain_microtasks()
        assert results == ["a", "b"]

    async def test_deep_reentrancy(self, sched):
        """Tasks can chain 10 levels deep and all execute."""
        depth = [0]

        def make_chain(n):
            async def task():
                depth[0] += 1
                if n > 0:
                    sched.queue_microtask(make_chain(n - 1))
            return task

        sched.queue_microtask(make_chain(9))
        await sched.drain_microtasks()
        assert depth[0] == 10

    async def test_microtask_exception_does_not_abort_drain(self, sched):
        """A raising microtask should not prevent subsequent ones from running."""
        results = []

        async def bad():
            raise RuntimeError("intentional")

        async def good():
            results.append("ok")

        sched.queue_microtask(bad)
        sched.queue_microtask(good)
        await sched.drain_microtasks()
        assert results == ["ok"]

    async def test_drain_clears_queue(self, sched):
        async def noop():
            pass
        sched.queue_microtask(noop)
        await sched.drain_microtasks()
        assert sched.stats()["microtask_queue_len"] == 0

    async def test_drain_returns_count(self, sched):
        async def noop():
            pass
        for _ in range(7):
            sched.queue_microtask(noop)
        count = await sched.drain_microtasks()
        assert count == 7


# ===========================================================================
# set_timeout / clear_timeout
# ===========================================================================


class TestSetTimeout:
    async def test_returns_positive_int(self, sched):
        async def noop():
            pass
        tid = sched.set_timeout(noop, delay_ms=0)
        assert isinstance(tid, int)
        assert tid > 0

    async def test_ids_are_unique(self, sched):
        async def noop():
            pass
        ids = {sched.set_timeout(noop, delay_ms=0) for _ in range(10)}
        assert len(ids) == 10

    async def test_timer_appears_in_timer_wheel(self, sched):
        async def noop():
            pass
        sched.set_timeout(noop, delay_ms=100)
        assert sched.pending_timer_count == 1
        assert len(sched.timer_wheel) == 1

    async def test_zero_delay_fires_immediately(self, sched):
        fired = []

        async def cb():
            fired.append(True)

        sched.set_timeout(cb, delay_ms=0)
        await sched.run_macrotasks(max_wait_ms=10)
        assert fired == [True]

    async def test_clear_timeout_prevents_firing(self, sched):
        results = []

        async def cb():
            results.append(1)

        tid = sched.set_timeout(cb, delay_ms=0)
        sched.clear_timeout(tid)
        await sched.run_macrotasks(max_wait_ms=10)
        assert results == []

    async def test_clear_nonexistent_is_noop(self, sched):
        sched.clear_timeout(9999)  # should not raise

    async def test_pending_timer_count_after_clear(self, sched):
        async def noop():
            pass
        tid = sched.set_timeout(noop, delay_ms=0)
        assert sched.pending_timer_count == 1
        sched.clear_timeout(tid)
        assert sched.pending_timer_count == 0

    async def test_negative_delay_treated_as_zero(self, sched):
        fired = []

        async def cb():
            fired.append(True)

        sched.set_timeout(cb, delay_ms=-100)
        await sched.run_macrotasks(max_wait_ms=10)
        assert fired == [True]

    async def test_stats_after_timeout_registered(self, sched):
        async def noop():
            pass
        sched.set_timeout(noop, delay_ms=5000)
        s = sched.stats()
        assert s["pending_timers"] == 1
        assert s["total_heap_entries"] == 1
        assert s["timer_counter"] == 1


# ===========================================================================
# set_interval / clear_interval
# ===========================================================================


class TestSetInterval:
    async def test_returns_positive_int(self, sched):
        async def noop():
            pass
        iid = sched.set_interval(noop, interval_ms=10)
        assert isinstance(iid, int) and iid > 0

    async def test_clear_interval_alias(self, sched):
        results = []

        async def cb():
            results.append(1)

        iid = sched.set_interval(cb, interval_ms=0)
        sched.clear_interval(iid)
        await sched.run_macrotasks(max_wait_ms=20)
        assert results == []

    async def test_interval_fires_multiple_times(self, sched):
        """Verify interval re-queues itself (fire twice within window)."""
        fired = []

        async def cb():
            fired.append(time.monotonic())

        # Set interval to fire as soon as possible (delay 0 effectively)
        sched.set_interval(cb, interval_ms=0)
        # Run macrotasks with a window wide enough for 2 re-queued firings
        for _ in range(3):
            await sched.run_macrotasks(max_wait_ms=50)

        # Should have fired at least twice
        assert len(fired) >= 2

    async def test_clear_interval_stops_recurrence(self, sched):
        fired_count = [0]
        iid_box = [None]

        async def cb():
            fired_count[0] += 1
            if fired_count[0] >= 2:
                sched.clear_interval(iid_box[0])

        iid_box[0] = sched.set_interval(cb, interval_ms=0)
        for _ in range(5):
            await sched.run_macrotasks(max_wait_ms=50)
        # Should have fired exactly 2 times then stopped
        assert fired_count[0] == 2

    async def test_minimum_interval_is_1ms(self, sched):
        """set_interval(cb, 0) is clamped to 1ms to avoid spinning."""
        async def noop():
            pass
        sched.set_interval(noop, interval_ms=0)
        entry = sched.timer_wheel[0]
        assert entry.interval_ms >= 1


# ===========================================================================
# run_macrotasks
# ===========================================================================


class TestRunMacrotasks:
    async def test_no_timers_returns_zero(self, sched):
        count = await sched.run_macrotasks()
        assert count == 0

    async def test_fires_elapsed_timers(self, sched):
        results = []

        async def cb():
            results.append(1)

        sched.set_timeout(cb, delay_ms=0)
        fired = await sched.run_macrotasks(max_wait_ms=50)
        assert fired == 1
        assert results == [1]

    async def test_future_timers_not_fired(self, sched):
        results = []

        async def cb():
            results.append(1)

        sched.set_timeout(cb, delay_ms=60_000)  # 60 seconds in future
        fired = await sched.run_macrotasks(max_wait_ms=10)
        assert fired == 0
        assert results == []

    async def test_macrotask_can_enqueue_microtasks(self, sched):
        """Macrotask fires -> enqueues microtask -> microtask drains within same run."""
        micro_ran = []
        macro_ran = []

        async def macro_cb():
            macro_ran.append(True)

            async def micro():
                micro_ran.append(True)

            sched.queue_microtask(micro)

        sched.set_timeout(macro_cb, delay_ms=0)
        await sched.run_macrotasks(max_wait_ms=10)

        assert macro_ran == [True]
        assert micro_ran == [True]

    async def test_multiple_timers_ordered(self, sched):
        """Timers fire in chronological order even if registered out of order."""
        order = []

        async def cb_b():
            order.append("b")

        async def cb_a():
            order.append("a")

        # Register b first (higher delay), then a (lower)
        sched.set_timeout(cb_b, delay_ms=1)
        sched.set_timeout(cb_a, delay_ms=0)
        # Both should fire (0ms and 1ms are both within a 50ms window)
        await asyncio.sleep(0.002)
        await sched.run_macrotasks(max_wait_ms=50)
        # a (lower delay) should fire before b
        assert order.index("a") < order.index("b")

    async def test_cancelled_timer_skipped(self, sched):
        results = []

        async def cb():
            results.append(1)

        tid = sched.set_timeout(cb, delay_ms=0)
        sched.clear_timeout(tid)
        await sched.run_macrotasks(max_wait_ms=10)
        assert results == []

    async def test_exception_in_macrotask_does_not_abort(self, sched):
        results = []

        async def bad():
            raise ValueError("boom")

        async def good():
            results.append("ok")

        sched.set_timeout(bad, delay_ms=0)
        sched.set_timeout(good, delay_ms=0)
        await sched.run_macrotasks(max_wait_ms=10)
        assert results == ["ok"]


# ===========================================================================
# run_transaction
# ===========================================================================


class TestRunTransaction:
    async def test_returns_action_result(self, sched):
        async def action():
            return "result_value"

        result = await sched.run_transaction(action())
        assert result == "result_value"

    async def test_drains_microtasks_after_action(self, sched):
        micro_ran = []

        async def action():
            async def micro():
                micro_ran.append(True)

            sched.queue_microtask(micro)
            return "ok"

        await sched.run_transaction(action())
        assert micro_ran == [True]

    async def test_flushes_mutations_after_action(self, sched):
        flush_ran = []

        async def mutation_flush():
            flush_ran.append(True)

        sched.register_mutation_flush(mutation_flush)

        async def action():
            return None

        await sched.run_transaction(action())
        assert flush_ran == [True]

    async def test_settles_network_after_action(self, sched):
        settle_ran = []

        async def settle():
            settle_ran.append(True)

        sched.register_network_settle(settle)

        async def action():
            return None

        await sched.run_transaction(action())
        assert settle_ran == [True]

    async def test_full_pipeline_order(self, sched):
        """Verify: action -> microtask -> network_settle -> mutation_flush."""
        events = []

        async def action():
            events.append("action")

            async def micro():
                events.append("micro")

            sched.queue_microtask(micro)

        async def net_settle():
            events.append("net")

        async def dom_flush():
            events.append("dom")

        sched.register_network_settle(net_settle)
        sched.register_mutation_flush(dom_flush)

        await sched.run_transaction(action())
        assert events == ["action", "micro", "net", "dom"]

    async def test_network_timeout_is_non_fatal(self, sched):
        """A network settle that never completes should not crash run_transaction."""
        async def never_settles():
            await asyncio.sleep(100)

        sched.register_network_settle(never_settles)

        async def action():
            return "done"

        result = await asyncio.wait_for(
            sched.run_transaction(action(), network_timeout=0.05),
            timeout=1.0,
        )
        assert result == "done"


# ===========================================================================
# settle_network
# ===========================================================================


class TestSettleNetwork:
    async def test_no_callbacks_completes_immediately(self, sched):
        await asyncio.wait_for(sched.settle_network(timeout=1.0), timeout=2.0)

    async def test_callback_is_awaited(self, sched):
        called = []

        async def cb():
            called.append(True)

        sched.register_network_settle(cb)
        await sched.settle_network(timeout=1.0)
        assert called == [True]

    async def test_multiple_callbacks_all_called(self, sched):
        called = []

        for i in range(3):
            async def cb(n=i):
                called.append(n)

            sched.register_network_settle(cb)

        await sched.settle_network(timeout=1.0)
        assert sorted(called) == [0, 1, 2]

    async def test_timeout_does_not_raise(self, sched):
        async def slow():
            await asyncio.sleep(100)

        sched.register_network_settle(slow)
        # Should not raise even though it times out
        await sched.settle_network(timeout=0.02)

    async def test_exception_in_callback_does_not_raise(self, sched):
        """return_exceptions=True means settle_network never raises."""
        async def bad():
            raise RuntimeError("network failed")

        sched.register_network_settle(bad)
        await sched.settle_network(timeout=1.0)  # should not raise


# ===========================================================================
# flush_dom_mutations
# ===========================================================================


class TestFlushDomMutations:
    async def test_no_callbacks_returns_zero(self, sched):
        count = await sched.flush_dom_mutations()
        assert count == 0

    async def test_single_callback_invoked(self, sched):
        called = []

        async def flush():
            called.append(True)

        sched.register_mutation_flush(flush)
        count = await sched.flush_dom_mutations()
        assert called == [True]
        assert count == 1

    async def test_multiple_callbacks_all_invoked(self, sched):
        called = []

        for i in range(4):
            async def cb(n=i):
                called.append(n)
            sched.register_mutation_flush(cb)

        await sched.flush_dom_mutations()
        assert sorted(called) == [0, 1, 2, 3]

    async def test_exception_in_callback_does_not_abort(self, sched):
        results = []

        async def bad():
            raise RuntimeError("dom error")

        async def good():
            results.append("ok")

        sched.register_mutation_flush(bad)
        sched.register_mutation_flush(good)
        await sched.flush_dom_mutations()
        assert results == ["ok"]

    async def test_flush_drains_microtasks_after(self, sched):
        """Mutation observer callbacks may enqueue microtasks."""
        micro_ran = []

        async def micro():
            micro_ran.append(True)

        async def flush():
            sched.queue_microtask(micro)

        sched.register_mutation_flush(flush)
        await sched.flush_dom_mutations()
        assert micro_ran == [True]


# ===========================================================================
# reset
# ===========================================================================


class TestReset:
    async def test_reset_clears_microtask_queue(self, sched):
        async def noop():
            pass
        sched.queue_microtask(noop)
        sched.reset()
        assert sched.stats()["microtask_queue_len"] == 0

    async def test_reset_clears_timer_heap(self, sched):
        async def noop():
            pass
        sched.set_timeout(noop, delay_ms=100)
        sched.reset()
        assert sched.stats()["pending_timers"] == 0

    async def test_reset_clears_network_callbacks(self, sched):
        async def cb():
            pass
        sched.register_network_settle(cb)
        sched.reset()
        assert sched.stats()["network_settle_callbacks"] == 0

    async def test_reset_clears_mutation_callbacks(self, sched):
        async def cb():
            pass
        sched.register_mutation_flush(cb)
        sched.reset()
        assert sched.stats()["mutation_flush_callbacks"] == 0

    async def test_reset_preserves_timer_counter(self, sched):
        """Timer IDs must remain unique across navigations."""
        async def noop():
            pass
        sched.set_timeout(noop)
        counter_before_reset = sched.stats()["timer_counter"]
        sched.reset()
        # Counter is not reset
        assert sched.stats()["timer_counter"] == counter_before_reset

    async def test_reset_does_not_fire_cancelled_timers(self, sched):
        results = []

        async def cb():
            results.append(1)

        sched.set_timeout(cb, delay_ms=0)
        sched.reset()
        await sched.run_macrotasks(max_wait_ms=10)
        assert results == []


# ===========================================================================
# stats / introspection
# ===========================================================================


class TestStats:
    async def test_stats_after_queue_microtask(self, sched):
        async def noop():
            pass
        sched.queue_microtask(noop)
        assert sched.stats()["microtask_queue_len"] == 1

    async def test_stats_after_register_callbacks(self, sched):
        async def noop():
            pass
        sched.register_network_settle(noop)
        sched.register_mutation_flush(noop)
        s = sched.stats()
        assert s["network_settle_callbacks"] == 1
        assert s["mutation_flush_callbacks"] == 1

    async def test_pending_timer_count_ignores_cancelled(self, sched):
        async def noop():
            pass
        tid = sched.set_timeout(noop, delay_ms=1000)
        assert sched.pending_timer_count == 1
        sched.clear_timeout(tid)
        assert sched.pending_timer_count == 0


# ===========================================================================
# Integration: microtasks + macrotasks + transaction
# ===========================================================================


class TestIntegration:
    async def test_microtask_before_macrotask(self, sched):
        """Microtasks are always flushed before the next macrotask fires."""
        order = []

        async def macro():
            order.append("macro")

        async def micro():
            order.append("micro")

        sched.queue_microtask(micro)
        sched.set_timeout(macro, delay_ms=0)

        # drain_microtasks runs before run_macrotasks
        await sched.drain_microtasks()
        await sched.run_macrotasks(max_wait_ms=10)

        assert order == ["micro", "macro"]

    async def test_transaction_is_isolated(self):
        """Two independent schedulers don't share state."""
        s1 = EventLoopScheduler()
        s2 = EventLoopScheduler()

        results1, results2 = [], []

        async def task1():
            results1.append(1)

        async def task2():
            results2.append(2)

        s1.queue_microtask(task1)
        s2.queue_microtask(task2)

        await s1.drain_microtasks()
        assert results1 == [1]
        assert results2 == []  # s2 not drained yet

        await s2.drain_microtasks()
        assert results2 == [2]

    async def test_run_transaction_with_network_and_mutation(self, sched):
        log = []

        async def action():
            log.append("action")
            return 42

        async def net():
            log.append("net")

        async def dom():
            log.append("dom")

        sched.register_network_settle(net)
        sched.register_mutation_flush(dom)

        result = await sched.run_transaction(action())
        assert result == 42
        assert "action" in log
        assert "net" in log
        assert "dom" in log

    async def test_set_timeout_chain(self, sched):
        """A timer callback can register a new timer."""
        order = []

        async def second():
            order.append(2)

        async def first():
            order.append(1)
            sched.set_timeout(second, delay_ms=0)

        sched.set_timeout(first, delay_ms=0)
        await sched.run_macrotasks(max_wait_ms=50)
        # second fires in the next run_macrotasks call
        await sched.run_macrotasks(max_wait_ms=50)
        assert order == [1, 2]
