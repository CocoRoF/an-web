"""
Policy rules for AN-Web — domain allow/deny, rate limiting, navigation scope.

Design principle: policy is not post-processing.
All checks happen at action precondition, not aftermath.
Every check returns a PolicyCheckResult with full audit info.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse


# ── Result types ──────────────────────────────────────────────────────────────

class ViolationType(str, Enum):
    """Machine-readable category for a policy violation."""
    DOMAIN_DENIED        = "domain_denied"
    DOMAIN_NOT_ALLOWED   = "domain_not_allowed"
    SCHEME_DENIED        = "scheme_denied"
    RATE_LIMITED         = "rate_limited"
    SCOPE_EXCEEDED       = "scope_exceeded"
    APPROVAL_REQUIRED    = "approval_required"
    FORM_SUBMIT_DENIED   = "form_submit_denied"
    FILE_DOWNLOAD_DENIED = "file_download_denied"


@dataclass
class PolicyCheckResult:
    """
    Result of a policy check.

    ``allowed=True``  → action may proceed.
    ``allowed=False`` → action is blocked; inspect ``violation_type`` and
                        ``reason`` for details.
    When ``violation_type`` is ``APPROVAL_REQUIRED``, ``approval_id`` is set
    so the caller can wire up the ApprovalManager flow.
    """
    allowed: bool
    reason: str | None = None
    violation_type: ViolationType | None = None
    approval_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls) -> "PolicyCheckResult":
        return cls(allowed=True)

    @classmethod
    def block(
        cls,
        violation: ViolationType,
        reason: str,
        **details: Any,
    ) -> "PolicyCheckResult":
        return cls(
            allowed=False,
            reason=reason,
            violation_type=violation,
            details=details,
        )

    @classmethod
    def needs_approval(cls, approval_id: str, reason: str) -> "PolicyCheckResult":
        return cls(
            allowed=False,
            reason=reason,
            violation_type=ViolationType.APPROVAL_REQUIRED,
            approval_id=approval_id,
        )

    @property
    def blocked(self) -> bool:
        return not self.allowed


# ── Navigation scope ──────────────────────────────────────────────────────────

class NavigationScope(str, Enum):
    """
    Controls how far the agent is allowed to navigate from its starting point.

    UNRESTRICTED — any URL is allowed (subject to allow/deny lists).
    SAME_DOMAIN  — all subdomains of the initial domain are allowed.
    SAME_ORIGIN  — must match exact scheme + host + port.
    PREFIX       — URL must start with ``scope_prefix``.
    """
    UNRESTRICTED = "unrestricted"
    SAME_DOMAIN  = "same_domain"
    SAME_ORIGIN  = "same_origin"
    PREFIX       = "prefix"


# ── Policy rules ──────────────────────────────────────────────────────────────

@dataclass
class PolicyRules:
    """
    Full policy configuration for a session.

    Fields:
        allowed_domains         empty list = allow all (subject to denied_domains)
        denied_domains          always blocked, regardless of allowed_domains
        allowed_schemes         defaults to ["https", "http", "about"]
        max_requests_per_minute sliding-window rate limit
        max_requests_per_hour   sliding-window rate limit (longer horizon)
        navigation_scope        how far can the agent roam from initial_url
        scope_prefix            used when navigation_scope == PREFIX
        allow_file_download     whether to follow content-disposition: attachment
        allow_form_submission   whether POST form submits are allowed
        require_approval_for    action names that need explicit human approval
    """

    # Domain lists
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains:  list[str] = field(default_factory=list)

    # Scheme restriction
    allowed_schemes: list[str] = field(
        default_factory=lambda: ["https", "http", "about"]
    )

    # Rate limits (sliding window)
    max_requests_per_minute: int = 120
    max_requests_per_hour:   int = 1000

    # Navigation scope
    navigation_scope: NavigationScope = NavigationScope.UNRESTRICTED
    scope_prefix:     str | None = None   # used when scope == PREFIX
    _initial_url:     str | None = field(default=None, repr=False)

    # Feature flags
    allow_file_download:   bool = True
    allow_form_submission: bool = True

    # Actions requiring explicit approval
    require_approval_for: list[str] = field(default_factory=list)

    # Internal: sliding-window deques (timestamps as monotonic floats)
    _req_minute: deque = field(default_factory=deque, repr=False)
    _req_hour:   deque = field(default_factory=deque, repr=False)

    # ── Factories ─────────────────────────────────────────────────────────────

    @classmethod
    def default(cls) -> "PolicyRules":
        """Permissive defaults — suitable for development / testing."""
        return cls()

    @classmethod
    def strict(cls) -> "PolicyRules":
        """Conservative defaults — suitable for production AI agents."""
        return cls(
            max_requests_per_minute=30,
            max_requests_per_hour=200,
            allow_file_download=False,
            require_approval_for=["submit", "navigate"],
        )

    @classmethod
    def sandboxed(cls, allowed_domains: list[str]) -> "PolicyRules":
        """Locked-down policy — only the given domains are reachable."""
        return cls(
            allowed_domains=allowed_domains,
            allowed_schemes=["https"],
            max_requests_per_minute=60,
            max_requests_per_hour=400,
            allow_file_download=False,
            allow_form_submission=True,
            navigation_scope=NavigationScope.SAME_DOMAIN,
        )

    # ── Initial URL (scope anchor) ─────────────────────────────────────────────

    def set_initial_url(self, url: str) -> None:
        """Record the first URL visited — used as scope anchor."""
        if self._initial_url is None:
            self._initial_url = url

    # ── Domain helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_host(url: str) -> str:
        """Return lowercased hostname, or '' on parse error."""
        try:
            return (urlparse(url).hostname or "").lower()
        except Exception:
            return ""

    @staticmethod
    def _extract_scheme(url: str) -> str:
        """Return lowercased scheme."""
        try:
            return (urlparse(url).scheme or "").lower()
        except Exception:
            return ""

    @staticmethod
    def _extract_origin(url: str) -> str:
        """Return 'scheme://host:port' string."""
        try:
            p = urlparse(url)
            port = f":{p.port}" if p.port else ""
            return f"{p.scheme}://{p.hostname}{port}"
        except Exception:
            return ""

    def _host_matches(self, host: str, pattern: str) -> bool:
        """
        Return True if ``host`` matches ``pattern``.

        Matching rules:
          - Exact:     ``example.com`` matches only ``example.com``
          - Subdomain: ``example.com`` also matches ``sub.example.com``
          - Wildcard:  ``*.example.com`` is normalized same as ``example.com``
        """
        pattern = pattern.lower().lstrip("*").lstrip(".")
        return host == pattern or host.endswith(f".{pattern}")

    # ── Core check: URL ───────────────────────────────────────────────────────

    def is_url_allowed(self, url: str) -> bool:
        """Backward-compatible convenience wrapper — returns bool."""
        return self.check_url(url).allowed

    def check_url(self, url: str) -> PolicyCheckResult:
        """
        Full URL policy check.

        Checks (in order):
          1. Special URLs (about:blank, empty) → always allowed.
          2. Scheme restriction.
          3. Domain deny list.
          4. Domain allow list.
          5. Navigation scope.
        """
        # 1. Special / blank
        if not url or url in ("about:blank", "about:srcdoc"):
            return PolicyCheckResult.ok()

        scheme = self._extract_scheme(url)
        host   = self._extract_host(url)

        # 2. Scheme
        if scheme and scheme not in self.allowed_schemes:
            return PolicyCheckResult.block(
                ViolationType.SCHEME_DENIED,
                f"scheme '{scheme}' is not in allowed_schemes",
                url=url, scheme=scheme,
            )

        # 3. Deny list
        for denied in self.denied_domains:
            if self._host_matches(host, denied):
                return PolicyCheckResult.block(
                    ViolationType.DOMAIN_DENIED,
                    f"host '{host}' matches denied domain '{denied}'",
                    url=url, host=host, matched_pattern=denied,
                )

        # 4. Allow list (only if non-empty)
        if self.allowed_domains:
            matched = any(
                self._host_matches(host, pat)
                for pat in self.allowed_domains
            )
            if not matched:
                return PolicyCheckResult.block(
                    ViolationType.DOMAIN_NOT_ALLOWED,
                    f"host '{host}' is not in allowed_domains",
                    url=url, host=host,
                )

        # 5. Navigation scope
        scope_result = self._check_scope(url, host, scheme)
        if scope_result.blocked:
            return scope_result

        return PolicyCheckResult.ok()

    def _check_scope(self, url: str, host: str, scheme: str) -> PolicyCheckResult:
        """Check navigation_scope constraint."""
        if self.navigation_scope == NavigationScope.UNRESTRICTED:
            return PolicyCheckResult.ok()

        initial = self._initial_url
        if not initial:
            return PolicyCheckResult.ok()   # no anchor yet → allow first navigation

        if self.navigation_scope == NavigationScope.SAME_DOMAIN:
            init_host = self._extract_host(initial)
            if not self._hosts_same_domain(host, init_host):
                return PolicyCheckResult.block(
                    ViolationType.SCOPE_EXCEEDED,
                    f"host '{host}' is outside initial domain '{init_host}'",
                    url=url, initial_url=initial,
                )

        elif self.navigation_scope == NavigationScope.SAME_ORIGIN:
            if self._extract_origin(url) != self._extract_origin(initial):
                return PolicyCheckResult.block(
                    ViolationType.SCOPE_EXCEEDED,
                    f"URL '{url}' has a different origin from initial '{initial}'",
                    url=url, initial_url=initial,
                )

        elif self.navigation_scope == NavigationScope.PREFIX:
            prefix = self.scope_prefix or initial
            if not url.startswith(prefix):
                return PolicyCheckResult.block(
                    ViolationType.SCOPE_EXCEEDED,
                    f"URL '{url}' does not start with required prefix '{prefix}'",
                    url=url, prefix=prefix,
                )

        return PolicyCheckResult.ok()

    @staticmethod
    def _hosts_same_domain(h1: str, h2: str) -> bool:
        """
        True if h1 and h2 share the same registrable domain.

        Simple heuristic: last two labels must match.
        e.g. 'sub.example.com' and 'other.example.com' → same domain.
        """
        def _root(host: str) -> str:
            parts = host.rstrip(".").split(".")
            return ".".join(parts[-2:]) if len(parts) >= 2 else host
        return _root(h1) == _root(h2)

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def check_rate_limit(self) -> bool:
        """Backward-compatible — consume one token and return True if allowed."""
        return self.consume_rate_limit().allowed

    def consume_rate_limit(self) -> PolicyCheckResult:
        """
        Sliding-window rate limiter (per-minute and per-hour).

        Mutates internal deques — call ONCE per actual network request.
        Returns a PolicyCheckResult (blocked if either window is full).
        """
        now = time.monotonic()

        # Evict expired entries
        minute_cutoff = now - 60.0
        hour_cutoff   = now - 3600.0
        while self._req_minute and self._req_minute[0] <= minute_cutoff:
            self._req_minute.popleft()
        while self._req_hour and self._req_hour[0] <= hour_cutoff:
            self._req_hour.popleft()

        # Check limits BEFORE consuming
        if len(self._req_minute) >= self.max_requests_per_minute:
            return PolicyCheckResult.block(
                ViolationType.RATE_LIMITED,
                f"rate limit exceeded: {len(self._req_minute)} requests in last 60s "
                f"(max {self.max_requests_per_minute})",
                limit="per_minute",
                current=len(self._req_minute),
                max=self.max_requests_per_minute,
            )

        if len(self._req_hour) >= self.max_requests_per_hour:
            return PolicyCheckResult.block(
                ViolationType.RATE_LIMITED,
                f"rate limit exceeded: {len(self._req_hour)} requests in last 3600s "
                f"(max {self.max_requests_per_hour})",
                limit="per_hour",
                current=len(self._req_hour),
                max=self.max_requests_per_hour,
            )

        # Consume
        self._req_minute.append(now)
        self._req_hour.append(now)
        return PolicyCheckResult.ok()

    def reset_rate_limit(self) -> None:
        """Clear rate-limit counters (useful for tests)."""
        self._req_minute.clear()
        self._req_hour.clear()

    def request_count_in_last_minute(self) -> int:
        """Current number of requests tracked in the last 60 s (non-mutating)."""
        now = time.monotonic()
        cutoff = now - 60.0
        return sum(1 for t in self._req_minute if t > cutoff)

    # ── Approval requirements ─────────────────────────────────────────────────

    def requires_approval(self, action_name: str) -> bool:
        """Return True if action_name needs explicit approval before execution."""
        return action_name in self.require_approval_for

    # ── Feature checks ────────────────────────────────────────────────────────

    def check_form_submission(self) -> PolicyCheckResult:
        if not self.allow_form_submission:
            return PolicyCheckResult.block(
                ViolationType.FORM_SUBMIT_DENIED,
                "form submission is disabled by policy",
            )
        return PolicyCheckResult.ok()

    def check_file_download(self) -> PolicyCheckResult:
        if not self.allow_file_download:
            return PolicyCheckResult.block(
                ViolationType.FILE_DOWNLOAD_DENIED,
                "file downloads are disabled by policy",
            )
        return PolicyCheckResult.ok()

    # ── Combined action check ─────────────────────────────────────────────────

    def check(
        self,
        action_name: str,
        url: str | None = None,
        consume_rate: bool = True,
    ) -> PolicyCheckResult:
        """
        Full check for a named action.

        Runs (in order):
          1. URL check (if url is provided).
          2. Rate-limit check (if consume_rate=True).
          3. Feature checks (submit, download).
          4. Approval gate.

        Note: approval_id is NOT set here — the caller must call
        ApprovalManager.request() to obtain an ID if desired.
        """
        # 1. URL
        if url is not None:
            result = self.check_url(url)
            if result.blocked:
                return result

        # 2. Rate limit
        if consume_rate:
            result = self.consume_rate_limit()
            if result.blocked:
                return result

        # 3. Feature flags
        if action_name == "submit":
            result = self.check_form_submission()
            if result.blocked:
                return result

        # 4. Approval gate
        if self.requires_approval(action_name):
            return PolicyCheckResult.block(
                ViolationType.APPROVAL_REQUIRED,
                f"action '{action_name}' requires explicit approval",
                action=action_name,
            )

        return PolicyCheckResult.ok()
