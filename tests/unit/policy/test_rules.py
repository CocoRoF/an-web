"""Unit tests for PolicyRules."""
from __future__ import annotations

import pytest
from an_web.policy.rules import PolicyRules


class TestPolicyRules:
    def test_default_allows_all_urls(self):
        policy = PolicyRules.default()
        assert policy.is_url_allowed("https://example.com") is True
        assert policy.is_url_allowed("https://github.com") is True

    def test_allowlist_blocks_unlisted(self):
        policy = PolicyRules(allowed_domains=["example.com"])
        assert policy.is_url_allowed("https://example.com") is True
        assert policy.is_url_allowed("https://other.com") is False

    def test_allowlist_allows_subdomains(self):
        policy = PolicyRules(allowed_domains=["example.com"])
        assert policy.is_url_allowed("https://sub.example.com") is True

    def test_denylist_blocks_domain(self):
        policy = PolicyRules(denied_domains=["evil.com"])
        assert policy.is_url_allowed("https://evil.com") is False
        assert policy.is_url_allowed("https://good.com") is True

    def test_denylist_blocks_subdomains(self):
        policy = PolicyRules(denied_domains=["evil.com"])
        assert policy.is_url_allowed("https://sub.evil.com") is False

    def test_about_blank_always_allowed(self):
        policy = PolicyRules(allowed_domains=["example.com"])
        assert policy.is_url_allowed("about:blank") is True

    def test_empty_url_allowed(self):
        policy = PolicyRules.default()
        assert policy.is_url_allowed("") is True

    def test_rate_limit_under_limit(self):
        policy = PolicyRules(max_requests_per_minute=10)
        for _ in range(9):
            assert policy.check_rate_limit() is True

    def test_rate_limit_exceeded(self):
        policy = PolicyRules(max_requests_per_minute=3)
        policy.check_rate_limit()
        policy.check_rate_limit()
        policy.check_rate_limit()
        # 4th call should be blocked
        assert policy.check_rate_limit() is False

    def test_requires_approval(self):
        policy = PolicyRules(require_approval_for=["submit", "navigate"])
        assert policy.requires_approval("submit") is True
        assert policy.requires_approval("click") is False

    def test_strict_policy(self):
        policy = PolicyRules.strict()
        assert policy.max_requests_per_minute == 30
        assert policy.allow_file_download is False
        assert "submit" in policy.require_approval_for
