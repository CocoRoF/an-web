"""Unit tests for PolicyRules — domain, rate limit, scope, features."""
from __future__ import annotations

import pytest
from an_web.policy.rules import (
    PolicyRules, PolicyCheckResult, NavigationScope, ViolationType,
)


# ── Factories ──────────────────────────────────────────────────────────────────

class TestFactories:
    def test_default_allows_all_urls(self):
        p = PolicyRules.default()
        assert p.is_url_allowed("https://example.com") is True
        assert p.is_url_allowed("https://github.com") is True

    def test_strict_policy(self):
        p = PolicyRules.strict()
        assert p.max_requests_per_minute == 30
        assert p.allow_file_download is False
        assert "submit" in p.require_approval_for

    def test_sandboxed_factory(self):
        p = PolicyRules.sandboxed(["example.com"])
        assert p.allowed_domains == ["example.com"]
        assert p.allowed_schemes == ["https"]
        assert p.allow_file_download is False

    def test_default_allowed_schemes(self):
        p = PolicyRules.default()
        assert "https" in p.allowed_schemes
        assert "http" in p.allowed_schemes
        assert "about" in p.allowed_schemes


# ── PolicyCheckResult ─────────────────────────────────────────────────────────

class TestPolicyCheckResult:
    def test_ok_result(self):
        r = PolicyCheckResult.ok()
        assert r.allowed is True
        assert r.blocked is False
        assert r.reason is None

    def test_block_result(self):
        r = PolicyCheckResult.block(ViolationType.DOMAIN_DENIED, "blocked")
        assert r.allowed is False
        assert r.blocked is True
        assert r.violation_type == ViolationType.DOMAIN_DENIED
        assert r.reason == "blocked"

    def test_needs_approval_result(self):
        r = PolicyCheckResult.needs_approval("req-001", "needs human OK")
        assert r.allowed is False
        assert r.approval_id == "req-001"
        assert r.violation_type == ViolationType.APPROVAL_REQUIRED

    def test_block_details_stored(self):
        r = PolicyCheckResult.block(
            ViolationType.DOMAIN_DENIED, "denied", url="https://evil.com", host="evil.com"
        )
        assert r.details["url"] == "https://evil.com"
        assert r.details["host"] == "evil.com"


# ── URL domain checks ─────────────────────────────────────────────────────────

class TestDomainChecks:
    def test_allowlist_blocks_unlisted(self):
        p = PolicyRules(allowed_domains=["example.com"])
        r = p.check_url("https://other.com")
        assert r.blocked
        assert r.violation_type == ViolationType.DOMAIN_NOT_ALLOWED

    def test_allowlist_allows_listed(self):
        p = PolicyRules(allowed_domains=["example.com"])
        assert p.check_url("https://example.com").allowed

    def test_allowlist_allows_subdomains(self):
        p = PolicyRules(allowed_domains=["example.com"])
        assert p.check_url("https://sub.example.com").allowed

    def test_allowlist_wildcard_prefix(self):
        p = PolicyRules(allowed_domains=["*.example.com"])
        assert p.check_url("https://sub.example.com").allowed

    def test_denylist_blocks_domain(self):
        p = PolicyRules(denied_domains=["evil.com"])
        r = p.check_url("https://evil.com")
        assert r.blocked
        assert r.violation_type == ViolationType.DOMAIN_DENIED

    def test_denylist_blocks_subdomains(self):
        p = PolicyRules(denied_domains=["evil.com"])
        assert p.check_url("https://sub.evil.com").blocked

    def test_denylist_wins_over_allowlist(self):
        p = PolicyRules(allowed_domains=["evil.com"], denied_domains=["evil.com"])
        assert p.check_url("https://evil.com").blocked

    def test_about_blank_always_allowed(self):
        p = PolicyRules(allowed_domains=["example.com"])
        assert p.check_url("about:blank").allowed

    def test_empty_url_allowed(self):
        p = PolicyRules.default()
        assert p.check_url("").allowed

    def test_backward_compat_is_url_allowed(self):
        p = PolicyRules(denied_domains=["evil.com"])
        assert p.is_url_allowed("https://good.com") is True
        assert p.is_url_allowed("https://evil.com") is False


# ── Scheme checks ─────────────────────────────────────────────────────────────

class TestSchemeChecks:
    def test_https_allowed_by_default(self):
        p = PolicyRules.default()
        assert p.check_url("https://example.com").allowed

    def test_http_allowed_by_default(self):
        p = PolicyRules.default()
        assert p.check_url("http://example.com").allowed

    def test_ftp_blocked_by_default(self):
        p = PolicyRules.default()
        r = p.check_url("ftp://files.example.com/file.txt")
        assert r.blocked
        assert r.violation_type == ViolationType.SCHEME_DENIED

    def test_javascript_scheme_blocked(self):
        p = PolicyRules.default()
        assert p.check_url("javascript:alert(1)").blocked

    def test_custom_allowed_scheme(self):
        p = PolicyRules(allowed_schemes=["https", "data"])
        assert p.check_url("data:text/plain,hello").allowed
        assert p.check_url("http://example.com").blocked


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_under_per_minute_limit(self):
        p = PolicyRules(max_requests_per_minute=10)
        for _ in range(9):
            assert p.consume_rate_limit().allowed

    def test_per_minute_limit_exceeded(self):
        p = PolicyRules(max_requests_per_minute=3)
        p.consume_rate_limit()
        p.consume_rate_limit()
        p.consume_rate_limit()
        r = p.consume_rate_limit()
        assert r.blocked
        assert r.violation_type == ViolationType.RATE_LIMITED
        assert "per_minute" in r.details.get("limit", "")

    def test_per_hour_limit_exceeded(self):
        p = PolicyRules(max_requests_per_minute=1000, max_requests_per_hour=2)
        p.consume_rate_limit()
        p.consume_rate_limit()
        r = p.consume_rate_limit()
        assert r.blocked
        assert "per_hour" in r.details.get("limit", "")

    def test_reset_rate_limit(self):
        p = PolicyRules(max_requests_per_minute=2)
        p.consume_rate_limit()
        p.consume_rate_limit()
        assert p.consume_rate_limit().blocked
        p.reset_rate_limit()
        assert p.consume_rate_limit().allowed

    def test_backward_compat_check_rate_limit(self):
        p = PolicyRules(max_requests_per_minute=3)
        assert p.check_rate_limit() is True
        assert p.check_rate_limit() is True
        assert p.check_rate_limit() is True
        assert p.check_rate_limit() is False

    def test_request_count_in_last_minute(self):
        p = PolicyRules.default()
        p.consume_rate_limit()
        p.consume_rate_limit()
        assert p.request_count_in_last_minute() == 2


# ── Navigation scope ──────────────────────────────────────────────────────────

class TestNavigationScope:
    def test_unrestricted_allows_any(self):
        p = PolicyRules(navigation_scope=NavigationScope.UNRESTRICTED)
        p.set_initial_url("https://start.com/page")
        assert p.check_url("https://totally-different.org/page").allowed

    def test_same_domain_allows_subdomains(self):
        p = PolicyRules(navigation_scope=NavigationScope.SAME_DOMAIN)
        p.set_initial_url("https://example.com/start")
        assert p.check_url("https://sub.example.com/page").allowed

    def test_same_domain_blocks_different_domain(self):
        p = PolicyRules(navigation_scope=NavigationScope.SAME_DOMAIN)
        p.set_initial_url("https://example.com/start")
        r = p.check_url("https://other.com/page")
        assert r.blocked
        assert r.violation_type == ViolationType.SCOPE_EXCEEDED

    def test_same_origin_allows_same(self):
        p = PolicyRules(navigation_scope=NavigationScope.SAME_ORIGIN)
        p.set_initial_url("https://example.com/start")
        assert p.check_url("https://example.com/other").allowed

    def test_same_origin_blocks_different_scheme(self):
        p = PolicyRules(navigation_scope=NavigationScope.SAME_ORIGIN)
        p.set_initial_url("https://example.com/start")
        r = p.check_url("http://example.com/other")
        assert r.blocked
        assert r.violation_type == ViolationType.SCOPE_EXCEEDED

    def test_prefix_scope_allows_matching(self):
        p = PolicyRules(
            navigation_scope=NavigationScope.PREFIX,
            scope_prefix="https://app.example.com/admin/",
        )
        p.set_initial_url("https://app.example.com/admin/dashboard")
        assert p.check_url("https://app.example.com/admin/users").allowed

    def test_prefix_scope_blocks_outside(self):
        p = PolicyRules(
            navigation_scope=NavigationScope.PREFIX,
            scope_prefix="https://app.example.com/admin/",
        )
        p.set_initial_url("https://app.example.com/admin/dashboard")
        r = p.check_url("https://app.example.com/public/home")
        assert r.blocked

    def test_no_initial_url_scope_allows_first_nav(self):
        """Before set_initial_url(), scope check always passes."""
        p = PolicyRules(navigation_scope=NavigationScope.SAME_DOMAIN)
        assert p.check_url("https://any-domain.com").allowed

    def test_set_initial_url_immutable_after_first_call(self):
        p = PolicyRules(navigation_scope=NavigationScope.SAME_DOMAIN)
        p.set_initial_url("https://first.com")
        p.set_initial_url("https://second.com")  # should be ignored
        assert p._initial_url == "https://first.com"


# ── Feature flags ──────────────────────────────────────────────────────────────

class TestFeatureFlags:
    def test_form_submission_allowed_by_default(self):
        p = PolicyRules.default()
        assert p.check_form_submission().allowed

    def test_form_submission_blocked(self):
        p = PolicyRules(allow_form_submission=False)
        r = p.check_form_submission()
        assert r.blocked
        assert r.violation_type == ViolationType.FORM_SUBMIT_DENIED

    def test_file_download_allowed_by_default(self):
        p = PolicyRules.default()
        assert p.check_file_download().allowed

    def test_file_download_blocked(self):
        p = PolicyRules(allow_file_download=False)
        r = p.check_file_download()
        assert r.blocked
        assert r.violation_type == ViolationType.FILE_DOWNLOAD_DENIED


# ── Approval requirements ─────────────────────────────────────────────────────

class TestApprovalRequirements:
    def test_requires_approval_true(self):
        p = PolicyRules(require_approval_for=["submit", "navigate"])
        assert p.requires_approval("submit") is True
        assert p.requires_approval("navigate") is True

    def test_requires_approval_false(self):
        p = PolicyRules(require_approval_for=["submit"])
        assert p.requires_approval("click") is False
        assert p.requires_approval("type") is False

    def test_requires_approval_blocks_in_check(self):
        p = PolicyRules(require_approval_for=["submit"])
        r = p.check("submit")
        assert r.blocked
        assert r.violation_type == ViolationType.APPROVAL_REQUIRED


# ── Combined check() ──────────────────────────────────────────────────────────

class TestCombinedCheck:
    def test_ok_for_clean_action(self):
        p = PolicyRules.default()
        assert p.check("click").allowed

    def test_url_check_in_combined(self):
        p = PolicyRules(denied_domains=["evil.com"])
        r = p.check("navigate", url="https://evil.com")
        assert r.blocked
        assert r.violation_type == ViolationType.DOMAIN_DENIED

    def test_rate_limit_in_combined(self):
        p = PolicyRules(max_requests_per_minute=2)
        p.check("navigate", url="https://a.com")
        p.check("navigate", url="https://b.com")
        r = p.check("navigate", url="https://c.com")
        assert r.blocked
        assert r.violation_type == ViolationType.RATE_LIMITED

    def test_no_rate_consume_when_disabled(self):
        p = PolicyRules(max_requests_per_minute=1)
        p.check("click", consume_rate=False)
        p.check("click", consume_rate=False)
        # Both should pass since we didn't consume the limit
        assert p.check("navigate", url="https://x.com", consume_rate=True).allowed
