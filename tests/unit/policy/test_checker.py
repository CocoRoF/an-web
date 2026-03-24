"""Unit tests for PolicyChecker — unified policy enforcement."""
from __future__ import annotations

import pytest
from an_web.policy.checker import PolicyChecker
from an_web.policy.rules import PolicyRules, ViolationType, NavigationScope
from an_web.policy.sandbox import Sandbox, SandboxLimits, Resource
from an_web.policy.approvals import ApprovalManager


# ── Helpers ────────────────────────────────────────────────────────────────────

def _checker(
    allowed_domains=None,
    denied_domains=None,
    require_approval_for=None,
    max_rpm=1000,
    allow_submit=True,
    scope=NavigationScope.UNRESTRICTED,
    sandbox_limits=None,
    auto_approve=False,
) -> PolicyChecker:
    rules = PolicyRules(
        allowed_domains=allowed_domains or [],
        denied_domains=denied_domains or [],
        require_approval_for=require_approval_for or [],
        max_requests_per_minute=max_rpm,
        allow_form_submission=allow_submit,
        navigation_scope=scope,
    )
    limits = sandbox_limits or SandboxLimits.unlimited()
    sandbox = Sandbox("test-session", limits=limits)
    approvals = ApprovalManager(auto_approve=auto_approve)
    return PolicyChecker(rules=rules, sandbox=sandbox, approvals=approvals)


# ── Factory methods ───────────────────────────────────────────────────────────

class TestFactories:
    def test_noop_allows_everything(self):
        checker = PolicyChecker.noop()
        assert checker.check_action("navigate", url="https://evil.com").allowed
        assert checker.check_action("submit").allowed
        assert checker.check_action("click").allowed

    def test_for_session_creates_checker(self):
        """for_session() should not raise when sandbox is absent from session."""
        class FakeSession:
            session_id = "fake-001"
            policy = PolicyRules.default()
            sandbox = None
            approvals = None

        checker = PolicyChecker.for_session(FakeSession())
        assert checker.check_action("click").allowed


# ── URL checks ────────────────────────────────────────────────────────────────

class TestURLChecks:
    def test_allowed_domain_passes(self):
        c = _checker(allowed_domains=["example.com"])
        assert c.check_action("navigate", url="https://example.com/page").allowed

    def test_denied_domain_blocked(self):
        c = _checker(denied_domains=["evil.com"])
        r = c.check_action("navigate", url="https://evil.com")
        assert r.blocked
        assert r.violation_type == ViolationType.DOMAIN_DENIED

    def test_scheme_blocked(self):
        c = _checker()
        r = c.check_action("navigate", url="ftp://files.example.com")
        assert r.blocked
        assert r.violation_type == ViolationType.SCHEME_DENIED

    def test_sandbox_host_block_checked_first(self):
        c = _checker()
        c.sandbox.block_host("sandbox-blocked.com")
        r = c.check_action("navigate", url="https://sandbox-blocked.com")
        assert r.blocked
        # Sandbox block triggers DOMAIN_DENIED
        assert r.violation_type == ViolationType.DOMAIN_DENIED

    def test_about_blank_always_allowed(self):
        c = _checker(denied_domains=["example.com"])
        assert c.check_action("navigate", url="about:blank").allowed


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_rate_limit_consumed_on_action(self):
        c = _checker(max_rpm=3)
        c.check_action("navigate", url="https://a.com")
        c.check_action("navigate", url="https://b.com")
        c.check_action("navigate", url="https://c.com")
        r = c.check_action("navigate", url="https://d.com")
        assert r.blocked
        assert r.violation_type == ViolationType.RATE_LIMITED

    def test_no_rate_consume_when_disabled(self):
        c = _checker(max_rpm=1)
        # Consume the one allowed
        c.check_action("navigate", url="https://a.com")
        # Now without consuming, this should still pass
        r = c.check_action("click", consume_resources=False)
        assert r.allowed


# ── Approval gate ──────────────────────────────────────────────────────────────

class TestApprovalGate:
    def test_action_requiring_approval_blocked(self):
        c = _checker(require_approval_for=["submit"])
        r = c.check_action("submit")
        assert r.blocked
        assert r.violation_type == ViolationType.APPROVAL_REQUIRED
        assert r.approval_id is not None

    def test_approval_request_created(self):
        c = _checker(require_approval_for=["submit"])
        r = c.check_action("submit")
        # The approval_id should be in the approval manager's pending
        assert c.approvals.has_pending("submit")

    def test_grant_once_allows_action(self):
        c = _checker(require_approval_for=["submit"])
        c.approvals.grant_once("submit")
        r = c.check_action("submit")
        assert r.allowed

    def test_auto_approve_bypasses_approval_gate(self):
        c = PolicyChecker(
            rules=PolicyRules(require_approval_for=["submit"]),
            sandbox=Sandbox("s", limits=SandboxLimits.unlimited()),
            approvals=ApprovalManager(auto_approve=True),
        )
        assert c.check_action("submit").allowed

    def test_approval_consumed_only_once(self):
        c = _checker(require_approval_for=["submit"])
        c.approvals.grant_once("submit")
        assert c.check_action("submit").allowed   # grant consumed
        r = c.check_action("submit")
        assert r.blocked  # no more grants


# ── Form submission ───────────────────────────────────────────────────────────

class TestFormSubmission:
    def test_submit_blocked_when_disabled(self):
        c = _checker(allow_submit=False)
        r = c.check_action("submit")
        assert r.blocked
        assert r.violation_type == ViolationType.FORM_SUBMIT_DENIED

    def test_submit_allowed_when_enabled(self):
        c = _checker(allow_submit=True)
        assert c.check_action("submit").allowed


# ── Sandbox resource limits ────────────────────────────────────────────────────

class TestSandboxIntegration:
    def test_navigation_counter_consumed(self):
        sb = Sandbox("s", limits=SandboxLimits(
            max_requests=100, max_navigations=2,
            max_dom_nodes=0, max_script_ops=0, max_snapshots=0
        ))
        c = PolicyChecker(rules=PolicyRules.default(), sandbox=sb)
        c.check_action("navigate", url="https://a.com")
        c.check_action("navigate", url="https://b.com")
        r = c.check_action("navigate", url="https://c.com")
        assert r.blocked

    def test_request_counter_consumed_with_url(self):
        sb = Sandbox("s", limits=SandboxLimits(
            max_requests=2, max_navigations=0,
            max_dom_nodes=0, max_script_ops=0, max_snapshots=0
        ))
        c = PolicyChecker(rules=PolicyRules.default(), sandbox=sb)
        c.check_action("navigate", url="https://a.com")
        c.check_action("navigate", url="https://b.com")
        r = c.check_action("navigate", url="https://c.com")
        assert r.blocked

    def test_no_resource_consumed_without_url(self):
        sb = Sandbox("s", limits=SandboxLimits(max_requests=1, max_navigations=0,
            max_dom_nodes=0, max_script_ops=0, max_snapshots=0))
        c = PolicyChecker(rules=PolicyRules.default(), sandbox=sb)
        # click without URL should not consume the request counter
        c.check_action("click")
        c.check_action("click")
        # request counter should still be 0
        assert sb.get_counter(Resource.REQUESTS) == 0


# ── Navigation scope ──────────────────────────────────────────────────────────

class TestNavigationScope:
    def test_scope_anchor_set_on_navigate(self):
        c = _checker(scope=NavigationScope.SAME_DOMAIN)
        c.check_action("navigate", url="https://example.com/start")
        assert c.rules._initial_url == "https://example.com/start"

    def test_second_navigate_scope_checked(self):
        c = _checker(scope=NavigationScope.SAME_DOMAIN)
        c.check_action("navigate", url="https://example.com/start")
        r = c.check_action("navigate", url="https://other.com/page")
        assert r.blocked
        assert r.violation_type == ViolationType.SCOPE_EXCEEDED

    def test_same_domain_subdomain_allowed(self):
        c = _checker(scope=NavigationScope.SAME_DOMAIN)
        c.check_action("navigate", url="https://example.com/start")
        r = c.check_action("navigate", url="https://sub.example.com/page")
        assert r.allowed


# ── Convenience wrappers ──────────────────────────────────────────────────────

class TestConvenienceWrappers:
    def test_check_navigate(self):
        c = _checker(denied_domains=["evil.com"])
        assert c.check_navigate("https://good.com").allowed
        assert c.check_navigate("https://evil.com").blocked

    def test_check_submit(self):
        c = _checker(allow_submit=False)
        assert c.check_submit().blocked

    def test_check_script_consumes_script_ops(self):
        sb = Sandbox("s", limits=SandboxLimits(
            max_script_ops=2, max_requests=0,
            max_navigations=0, max_dom_nodes=0, max_snapshots=0
        ))
        c = PolicyChecker(rules=PolicyRules.default(), sandbox=sb)
        c.check_script()
        c.check_script()
        r = c.check_script()
        assert r.blocked


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_returns_dict(self):
        c = _checker(allowed_domains=["example.com"])
        status = c.status()
        assert "rules" in status
        assert "sandbox" in status
        assert "approvals" in status

    def test_status_rules_content(self):
        c = _checker(allowed_domains=["example.com"])
        assert c.status()["rules"]["allowed_domains"] == ["example.com"]
