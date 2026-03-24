"""
Execution sandbox per session — resource limits and isolation.

Each session gets its own Sandbox instance that tracks resource consumption
and enforces hard limits to prevent runaway AI agents.

Resource categories:
    requests    — outbound HTTP requests
    dom_nodes   — total DOM nodes parsed / created
    script_ops  — JS eval operations
    navigations — page navigations
    snapshots   — snapshot records created
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from an_web.policy.rules import PolicyCheckResult, ViolationType

# ── Resource limits ───────────────────────────────────────────────────────────

@dataclass
class SandboxLimits:
    """
    Hard resource ceilings for a session sandbox.

    Set any value to 0 to disable that limit.
    """
    max_requests:    int = 500     # total HTTP requests
    max_dom_nodes:   int = 50_000  # cumulative DOM nodes parsed
    max_script_ops:  int = 1_000   # JS eval operations
    max_navigations: int = 100     # total page loads
    max_snapshots:   int = 200     # snapshot records

    @classmethod
    def default(cls) -> SandboxLimits:
        return cls()

    @classmethod
    def strict(cls) -> SandboxLimits:
        return cls(
            max_requests=100,
            max_dom_nodes=20_000,
            max_script_ops=200,
            max_navigations=20,
            max_snapshots=50,
        )

    @classmethod
    def unlimited(cls) -> SandboxLimits:
        """Disable all resource limits (for testing / offline use)."""
        return cls(
            max_requests=0,
            max_dom_nodes=0,
            max_script_ops=0,
            max_navigations=0,
            max_snapshots=0,
        )


# ── Resource names (string constants for consistent lookup) ───────────────────

class Resource:
    REQUESTS    = "requests"
    DOM_NODES   = "dom_nodes"
    SCRIPT_OPS  = "script_ops"
    NAVIGATIONS = "navigations"
    SNAPSHOTS   = "snapshots"


# ── Sandbox ───────────────────────────────────────────────────────────────────

class Sandbox:
    """
    Per-session execution isolation context.

    Tracks:
    - Resource consumption counters (requests, dom_nodes, script_ops, …)
    - Session-private key-value storage (separate from DOM localStorage)
    - Per-session blocked hosts (orthogonal to PolicyRules deny list)

    Usage::

        sandbox = Sandbox("sess-001", limits=SandboxLimits.strict())
        result = sandbox.consume(Resource.REQUESTS)
        if result.blocked:
            raise RuntimeError(result.reason)
    """

    def __init__(
        self,
        session_id: str,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.session_id = session_id
        self.limits     = limits or SandboxLimits.default()

        # Resource counters
        self._counters: dict[str, int] = {
            Resource.REQUESTS:    0,
            Resource.DOM_NODES:   0,
            Resource.SCRIPT_OPS:  0,
            Resource.NAVIGATIONS: 0,
            Resource.SNAPSHOTS:   0,
        }

        # Private sandbox storage
        self._storage: dict[str, str] = {}

        # Per-session additional blocked hosts (beyond PolicyRules)
        self._blocked_hosts: set[str] = set()

        # Metadata
        self._created_at: float = _now()
        self._last_activity: float = _now()

    # ── Resource tracking ─────────────────────────────────────────────────────

    def consume(self, resource: str, amount: int = 1) -> PolicyCheckResult:
        """
        Consume ``amount`` units of ``resource``.

        Returns:
            PolicyCheckResult.ok()      — within limits; counter updated.
            PolicyCheckResult.block(…)  — limit exceeded; counter NOT updated.
        """
        limit = self._get_limit(resource)
        current = self._counters.get(resource, 0)

        if limit > 0 and (current + amount) > limit:
            return PolicyCheckResult.block(
                ViolationType.RATE_LIMITED,
                f"sandbox resource limit exceeded: {resource} "
                f"({current + amount} > {limit})",
                resource=resource,
                current=current,
                requested=amount,
                limit=limit,
                session_id=self.session_id,
            )

        self._counters[resource] = current + amount
        self._last_activity = _now()
        return PolicyCheckResult.ok()

    def check(self, resource: str, amount: int = 1) -> PolicyCheckResult:
        """
        Check whether consuming ``amount`` would exceed the limit.

        Does NOT mutate counters.
        """
        limit = self._get_limit(resource)
        if limit <= 0:
            return PolicyCheckResult.ok()

        current = self._counters.get(resource, 0)
        if (current + amount) > limit:
            return PolicyCheckResult.block(
                ViolationType.RATE_LIMITED,
                f"sandbox resource limit would be exceeded: {resource} "
                f"({current + amount} > {limit})",
                resource=resource,
                current=current,
                requested=amount,
                limit=limit,
            )
        return PolicyCheckResult.ok()

    def _get_limit(self, resource: str) -> int:
        limits_map = {
            Resource.REQUESTS:    self.limits.max_requests,
            Resource.DOM_NODES:   self.limits.max_dom_nodes,
            Resource.SCRIPT_OPS:  self.limits.max_script_ops,
            Resource.NAVIGATIONS: self.limits.max_navigations,
            Resource.SNAPSHOTS:   self.limits.max_snapshots,
        }
        return limits_map.get(resource, 0)

    def get_counter(self, resource: str) -> int:
        """Return current consumption counter for a resource."""
        return self._counters.get(resource, 0)

    def reset_counter(self, resource: str) -> None:
        """Reset a single resource counter (for tests)."""
        if resource in self._counters:
            self._counters[resource] = 0

    def reset_all_counters(self) -> None:
        """Reset all resource counters (for tests)."""
        for key in self._counters:
            self._counters[key] = 0

    def counters_snapshot(self) -> dict[str, int]:
        """Return a copy of all resource counters."""
        return dict(self._counters)

    # ── Host blocking ─────────────────────────────────────────────────────────

    def block_host(self, host: str) -> None:
        """Add a host to the per-session block list."""
        self._blocked_hosts.add(host.lower())

    def unblock_host(self, host: str) -> None:
        """Remove a host from the per-session block list."""
        self._blocked_hosts.discard(host.lower())

    def is_host_blocked(self, host: str) -> bool:
        """Return True if host is sandbox-blocked."""
        return host.lower() in self._blocked_hosts

    def check_host(self, url: str) -> PolicyCheckResult:
        """Check if the host of ``url`` is sandbox-blocked."""
        from urllib.parse import urlparse
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            return PolicyCheckResult.ok()

        if host in self._blocked_hosts:
            return PolicyCheckResult.block(
                ViolationType.DOMAIN_DENIED,
                f"host '{host}' is blocked in session sandbox",
                host=host, session_id=self.session_id,
            )
        return PolicyCheckResult.ok()

    # ── Private storage ───────────────────────────────────────────────────────

    def get_storage(self, key: str) -> str | None:
        return self._storage.get(key)

    def set_storage(self, key: str, value: str) -> None:
        self._storage[key] = value
        self._last_activity = _now()

    def clear_storage(self) -> None:
        self._storage.clear()

    # ── Info ──────────────────────────────────────────────────────────────────

    def info(self) -> dict[str, Any]:
        """Return sandbox state summary."""
        return {
            "session_id":     self.session_id,
            "counters":       self.counters_snapshot(),
            "blocked_hosts":  sorted(self._blocked_hosts),
            "storage_keys":   list(self._storage.keys()),
            "last_activity":  self._last_activity,
        }

    def __repr__(self) -> str:
        counters = ", ".join(f"{k}={v}" for k, v in self._counters.items() if v)
        return f"Sandbox(session={self.session_id!r}, {counters or 'idle'})"


# ── Helper ────────────────────────────────────────────────────────────────────

def _now() -> float:
    import time
    return time.monotonic()
