"""Unit tests for Sandbox — resource limits and isolation."""
from __future__ import annotations

import pytest
from an_web.policy.sandbox import Sandbox, SandboxLimits, Resource
from an_web.policy.rules import ViolationType


# ── SandboxLimits ─────────────────────────────────────────────────────────────

class TestSandboxLimits:
    def test_default_limits_nonzero(self):
        lim = SandboxLimits.default()
        assert lim.max_requests > 0
        assert lim.max_dom_nodes > 0
        assert lim.max_script_ops > 0
        assert lim.max_navigations > 0
        assert lim.max_snapshots > 0

    def test_strict_limits_lower_than_default(self):
        d = SandboxLimits.default()
        s = SandboxLimits.strict()
        assert s.max_requests < d.max_requests
        assert s.max_navigations < d.max_navigations

    def test_unlimited_all_zero(self):
        lim = SandboxLimits.unlimited()
        assert lim.max_requests == 0
        assert lim.max_dom_nodes == 0
        assert lim.max_script_ops == 0
        assert lim.max_navigations == 0
        assert lim.max_snapshots == 0


# ── Resource consumption ──────────────────────────────────────────────────────

class TestResourceConsumption:
    def _sandbox(self, **overrides) -> Sandbox:
        limits = SandboxLimits(**overrides) if overrides else SandboxLimits.default()
        return Sandbox("test-session", limits=limits)

    def test_consume_within_limit(self):
        sb = self._sandbox(max_requests=10)
        for _ in range(5):
            assert sb.consume(Resource.REQUESTS).allowed

    def test_consume_at_limit(self):
        sb = self._sandbox(max_requests=3)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        r = sb.consume(Resource.REQUESTS)
        assert r.blocked
        assert r.violation_type == ViolationType.RATE_LIMITED

    def test_consume_returns_details(self):
        sb = self._sandbox(max_requests=2)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        r = sb.consume(Resource.REQUESTS)
        assert r.details["resource"] == Resource.REQUESTS
        assert r.details["limit"] == 2

    def test_unlimited_never_blocks(self):
        sb = Sandbox("test", limits=SandboxLimits.unlimited())
        for _ in range(10_000):
            assert sb.consume(Resource.REQUESTS).allowed

    def test_counter_increments(self):
        sb = self._sandbox(max_requests=100)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        assert sb.get_counter(Resource.REQUESTS) == 3

    def test_counter_not_incremented_when_blocked(self):
        sb = self._sandbox(max_requests=2)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)  # blocked
        # Counter stays at 2 (blocked call doesn't increment)
        assert sb.get_counter(Resource.REQUESTS) == 2

    def test_consume_multiple_resources_independently(self):
        sb = self._sandbox(max_requests=5, max_navigations=2)
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.NAVIGATIONS)
        sb.consume(Resource.NAVIGATIONS)
        r_nav = sb.consume(Resource.NAVIGATIONS)
        r_req = sb.consume(Resource.REQUESTS)
        assert r_nav.blocked
        assert r_req.allowed

    def test_dom_nodes_resource(self):
        sb = self._sandbox(max_dom_nodes=100)
        assert sb.consume(Resource.DOM_NODES, amount=50).allowed
        assert sb.consume(Resource.DOM_NODES, amount=50).allowed
        r = sb.consume(Resource.DOM_NODES, amount=1)
        assert r.blocked

    def test_script_ops_resource(self):
        sb = self._sandbox(max_script_ops=3)
        sb.consume(Resource.SCRIPT_OPS)
        sb.consume(Resource.SCRIPT_OPS)
        sb.consume(Resource.SCRIPT_OPS)
        assert sb.consume(Resource.SCRIPT_OPS).blocked

    def test_bulk_consume(self):
        sb = self._sandbox(max_requests=100)
        result = sb.consume(Resource.REQUESTS, amount=50)
        assert result.allowed
        assert sb.get_counter(Resource.REQUESTS) == 50

    def test_bulk_consume_exceeds_limit(self):
        sb = self._sandbox(max_requests=10)
        sb.consume(Resource.REQUESTS, amount=8)
        r = sb.consume(Resource.REQUESTS, amount=5)  # 8+5=13 > 10
        assert r.blocked
        assert sb.get_counter(Resource.REQUESTS) == 8  # unchanged


# ── Check (non-mutating) ──────────────────────────────────────────────────────

class TestCheck:
    def test_check_does_not_consume(self):
        sb = Sandbox("s", limits=SandboxLimits(max_requests=3))
        for _ in range(3):
            sb.check(Resource.REQUESTS)  # would block at 3, but doesn't count
        assert sb.get_counter(Resource.REQUESTS) == 0

    def test_check_returns_blocked_near_limit(self):
        sb = Sandbox("s", limits=SandboxLimits(max_requests=2))
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        r = sb.check(Resource.REQUESTS)
        assert r.blocked

    def test_check_unlimited_always_allowed(self):
        sb = Sandbox("s", limits=SandboxLimits.unlimited())
        assert sb.check(Resource.REQUESTS, amount=999999).allowed


# ── Reset ─────────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_single_counter(self):
        sb = Sandbox("s", limits=SandboxLimits(max_requests=3))
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.REQUESTS)
        sb.reset_counter(Resource.REQUESTS)
        assert sb.get_counter(Resource.REQUESTS) == 0
        assert sb.consume(Resource.REQUESTS).allowed

    def test_reset_all_counters(self):
        sb = Sandbox("s", limits=SandboxLimits(max_requests=3, max_navigations=2))
        sb.consume(Resource.REQUESTS)
        sb.consume(Resource.NAVIGATIONS)
        sb.reset_all_counters()
        assert sb.get_counter(Resource.REQUESTS) == 0
        assert sb.get_counter(Resource.NAVIGATIONS) == 0


# ── Host blocking ─────────────────────────────────────────────────────────────

class TestHostBlocking:
    def test_block_and_check_host(self):
        sb = Sandbox("s")
        sb.block_host("evil.com")
        assert sb.is_host_blocked("evil.com") is True
        assert sb.is_host_blocked("good.com") is False

    def test_check_host_url(self):
        sb = Sandbox("s")
        sb.block_host("evil.com")
        r = sb.check_host("https://evil.com/page")
        assert r.blocked
        assert r.violation_type == ViolationType.DOMAIN_DENIED

    def test_check_host_subdomain_blocked(self):
        sb = Sandbox("s")
        sb.block_host("sub.evil.com")
        r = sb.check_host("https://sub.evil.com/page")
        assert r.blocked

    def test_check_host_allowed(self):
        sb = Sandbox("s")
        sb.block_host("evil.com")
        assert sb.check_host("https://good.com/page").allowed

    def test_unblock_host(self):
        sb = Sandbox("s")
        sb.block_host("evil.com")
        sb.unblock_host("evil.com")
        assert sb.is_host_blocked("evil.com") is False

    def test_case_insensitive(self):
        sb = Sandbox("s")
        sb.block_host("Evil.COM")
        assert sb.is_host_blocked("evil.com") is True


# ── Private storage ───────────────────────────────────────────────────────────

class TestPrivateStorage:
    def test_get_set(self):
        sb = Sandbox("s")
        sb.set_storage("key", "value")
        assert sb.get_storage("key") == "value"

    def test_get_missing_returns_none(self):
        sb = Sandbox("s")
        assert sb.get_storage("missing") is None

    def test_clear_storage(self):
        sb = Sandbox("s")
        sb.set_storage("k1", "v1")
        sb.set_storage("k2", "v2")
        sb.clear_storage()
        assert sb.get_storage("k1") is None

    def test_overwrite(self):
        sb = Sandbox("s")
        sb.set_storage("key", "old")
        sb.set_storage("key", "new")
        assert sb.get_storage("key") == "new"


# ── Info / repr ───────────────────────────────────────────────────────────────

class TestInfo:
    def test_info_returns_dict(self):
        sb = Sandbox("s1")
        info = sb.info()
        assert info["session_id"] == "s1"
        assert "counters" in info
        assert "blocked_hosts" in info

    def test_counters_snapshot_is_copy(self):
        sb = Sandbox("s")
        snapshot = sb.counters_snapshot()
        snapshot[Resource.REQUESTS] = 999  # mutate snapshot
        assert sb.get_counter(Resource.REQUESTS) == 0  # original unchanged

    def test_repr(self):
        sb = Sandbox("my-session")
        r = repr(sb)
        assert "my-session" in r
