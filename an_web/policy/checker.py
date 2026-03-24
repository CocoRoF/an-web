"""
PolicyChecker — unified policy entry point for all action executions.

Combines PolicyRules + Sandbox + ApprovalManager into a single
check_action() call that actions invoke before execution.

Usage::

    checker = PolicyChecker.for_session(session)
    result = checker.check_action("navigate", url="https://example.com")
    if result.blocked:
        return _make_failure("navigate", result.reason)

    # If approval_required, wire up the approval flow:
    if result.violation_type == ViolationType.APPROVAL_REQUIRED:
        req_id = checker.approvals.request("navigate", {"url": url})
        # ... surface req_id to human, wait for grant ...
        checker.approvals.grant(req_id)
        # re-check (is_approved consumes the grant):
        if checker.approvals.is_approved("navigate"):
            proceed()
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from an_web.policy.rules import PolicyCheckResult, PolicyRules, ViolationType
from an_web.policy.sandbox import Sandbox, SandboxLimits, Resource
from an_web.policy.approvals import ApprovalManager

if TYPE_CHECKING:
    from an_web.core.session import Session


class PolicyChecker:
    """
    Unified policy enforcement point.

    All action preconditions route through here.
    Ordering:
      1. Sandbox host block (per-session IP-level block)
      2. PolicyRules URL check (domain allow/deny, scheme, scope)
      3. PolicyRules rate limit (per-minute / per-hour)
      4. Feature checks (form submission, file download)
      5. Approval gate (requires_approval_for list)
      6. Sandbox resource consumption (requests counter)
    """

    def __init__(
        self,
        rules:     PolicyRules | None = None,
        sandbox:   Sandbox | None = None,
        approvals: ApprovalManager | None = None,
    ) -> None:
        self.rules     = rules     or PolicyRules.default()
        self.sandbox   = sandbox   or Sandbox(session_id="__default__")
        self.approvals = approvals or ApprovalManager()

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def for_session(cls, session: "Session") -> "PolicyChecker":
        """
        Build a PolicyChecker wired to the session's policy + sandbox.

        Reads ``session.policy`` (PolicyRules) and ``session.sandbox``
        (Sandbox) if present; creates defaults otherwise.
        """
        rules = getattr(session, "policy", None) or PolicyRules.default()

        sandbox = getattr(session, "sandbox", None)
        if sandbox is None:
            sandbox = Sandbox(session_id=getattr(session, "session_id", "__default__"))

        approvals = getattr(session, "approvals", None) or ApprovalManager()

        return cls(rules=rules, sandbox=sandbox, approvals=approvals)

    @classmethod
    def noop(cls) -> "PolicyChecker":
        """
        A no-op checker that allows everything.

        Useful for unit tests that don't care about policy.
        """
        return cls(
            rules=PolicyRules.default(),
            sandbox=Sandbox("__noop__", limits=SandboxLimits.unlimited()),
            approvals=ApprovalManager(auto_approve=True),
        )

    # ── Core check ────────────────────────────────────────────────────────────

    def check_action(
        self,
        action_name: str,
        url: str | None = None,
        consume_resources: bool = True,
        details: dict[str, Any] | None = None,
    ) -> PolicyCheckResult:
        """
        Full pre-action policy check.

        Args:
            action_name:        e.g. "navigate", "submit", "click"
            url:                destination URL (for navigate / submit)
            consume_resources:  whether to consume rate-limit + sandbox counters
            details:            extra context for approval requests

        Returns:
            PolicyCheckResult.ok()     → proceed
            PolicyCheckResult.block()  → blocked; check .violation_type
        """
        # 1. Sandbox host block (no rate consumption — cheap check first)
        if url is not None:
            host_result = self.sandbox.check_host(url)
            if host_result.blocked:
                return host_result

        # 2. URL rules (domain, scheme, scope) — does NOT consume rate limit
        if url is not None:
            url_result = self.rules.check_url(url)
            if url_result.blocked:
                return url_result

            # Record scope anchor on first navigate
            if action_name == "navigate":
                self.rules.set_initial_url(url)

        # 3. Rate limit (consumes a token if allowed)
        if consume_resources:
            rate_result = self.rules.consume_rate_limit()
            if rate_result.blocked:
                return rate_result

        # 4. Feature checks
        if action_name == "submit":
            feature_result = self.rules.check_form_submission()
            if feature_result.blocked:
                return feature_result

        # 5. Approval gate
        if self.rules.requires_approval(action_name):
            # Check if a pre-approval / grant is already available
            if self.approvals.is_approved(action_name):
                # Approval consumed — proceed
                pass
            else:
                # Need human approval — return requires-approval result
                req_id = self.approvals.request(
                    action_name,
                    details or ({"url": url} if url else {}),
                )
                # If auto_approve, is_approved() would have returned True above.
                # If we get here, we truly need external approval.
                return PolicyCheckResult.needs_approval(
                    approval_id=req_id,
                    reason=f"action '{action_name}' requires explicit approval "
                           f"(request_id: {req_id})",
                )

        # 6. Sandbox resource consumption
        if consume_resources:
            if action_name in ("navigate",):
                nav_result = self.sandbox.consume(Resource.NAVIGATIONS)
                if nav_result.blocked:
                    return nav_result

            # Every action with a URL counts as a request
            if url is not None:
                req_result = self.sandbox.consume(Resource.REQUESTS)
                if req_result.blocked:
                    return req_result

        return PolicyCheckResult.ok()

    # ── Convenience wrappers ──────────────────────────────────────────────────

    def check_navigate(self, url: str) -> PolicyCheckResult:
        return self.check_action("navigate", url=url)

    def check_submit(self, url: str | None = None) -> PolicyCheckResult:
        return self.check_action("submit", url=url)

    def check_click(self) -> PolicyCheckResult:
        return self.check_action("click", consume_resources=False)

    def check_script(self) -> PolicyCheckResult:
        """Check + consume script_ops sandbox counter."""
        result = self.sandbox.consume(Resource.SCRIPT_OPS)
        if result.blocked:
            return result
        return PolicyCheckResult.ok()

    # ── Info ──────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return a diagnostic summary of the current policy state."""
        return {
            "rules": {
                "allowed_domains":  self.rules.allowed_domains,
                "denied_domains":   self.rules.denied_domains,
                "navigation_scope": self.rules.navigation_scope.value,
                "requests_per_min": self.rules.request_count_in_last_minute(),
                "max_per_minute":   self.rules.max_requests_per_minute,
            },
            "sandbox": self.sandbox.info(),
            "approvals": {
                "auto_approve": self.approvals.auto_approve,
                "pending":      len(self.approvals.all_pending()),
                "grants":       len(self.approvals._grants),
            },
        }
