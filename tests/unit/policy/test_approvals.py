"""Unit tests for ApprovalManager — request, grant, deny, auto-approve."""
from __future__ import annotations

import pytest
from an_web.policy.approvals import ApprovalManager, PendingApproval


# ── PendingApproval ───────────────────────────────────────────────────────────

class TestPendingApproval:
    def test_new_request_is_pending(self):
        req = PendingApproval(request_id="r1", action="submit", details={})
        assert req.is_pending() is True
        assert req.status == "pending"

    def test_not_expired_by_default(self):
        req = PendingApproval(request_id="r1", action="submit", details={})
        assert req.is_expired() is False

    def test_expired_if_expires_at_in_past(self):
        import time
        req = PendingApproval(
            request_id="r1", action="submit", details={},
            expires_at=time.monotonic() - 1.0,
        )
        assert req.is_expired() is True
        assert req.is_pending() is False


# ── Basic request/grant/deny ──────────────────────────────────────────────────

class TestRequestGrantDeny:
    def test_request_returns_id(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit")
        assert isinstance(req_id, str)
        assert len(req_id) > 0

    def test_request_stored_as_pending(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit", {"url": "https://example.com"})
        assert mgr.has_pending("submit")
        pending = mgr.pending_for("submit")
        assert len(pending) == 1
        assert pending[0].request_id == req_id

    def test_grant_approves_request(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit")
        assert mgr.grant(req_id) is True
        # is_approved should now return True (and consume it)
        assert mgr.is_approved("submit") is True

    def test_is_approved_consumes_one_time(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit")
        mgr.grant(req_id)
        # First check consumes it
        assert mgr.is_approved("submit") is True
        # Second check — consumed; no more
        assert mgr.is_approved("submit") is False

    def test_deny_removes_from_pending(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit")
        assert mgr.deny(req_id) is True
        assert not mgr.has_pending("submit")

    def test_is_not_approved_after_deny(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit")
        mgr.deny(req_id)
        assert mgr.is_approved("submit") is False

    def test_grant_unknown_id_returns_false(self):
        mgr = ApprovalManager()
        assert mgr.grant("nonexistent") is False

    def test_deny_unknown_id_returns_false(self):
        mgr = ApprovalManager()
        assert mgr.deny("nonexistent") is False

    def test_approve_backward_compat(self):
        mgr = ApprovalManager()
        req_id = mgr.request("navigate")
        assert mgr.approve(req_id) is True  # backward-compat alias
        assert mgr.is_approved("navigate") is True

    def test_request_approval_backward_compat(self):
        mgr = ApprovalManager()
        req_id = mgr.request_approval("submit", {"url": "https://example.com"})
        assert isinstance(req_id, str)


# ── Auto-approve mode ─────────────────────────────────────────────────────────

class TestAutoApprove:
    def test_auto_approve_off_by_default(self):
        mgr = ApprovalManager()
        assert mgr.auto_approve is False

    def test_set_auto_approve(self):
        mgr = ApprovalManager()
        mgr.set_auto_approve(True)
        assert mgr.auto_approve is True

    def test_auto_approve_returns_true_without_request(self):
        mgr = ApprovalManager(auto_approve=True)
        assert mgr.is_approved("submit") is True
        assert mgr.is_approved("navigate") is True
        assert mgr.is_approved("anything") is True

    def test_auto_approve_does_not_require_request(self):
        mgr = ApprovalManager(auto_approve=True)
        # No explicit request needed — is_approved always returns True
        assert mgr.is_approved("submit") is True

    def test_request_auto_approved_immediately(self):
        mgr = ApprovalManager(auto_approve=True)
        req_id = mgr.request("submit")
        # With auto_approve, the request itself is marked approved
        assert mgr._pending[req_id].status == "approved"


# ── Pre-approval grants ───────────────────────────────────────────────────────

class TestPreApprovalGrants:
    def test_grant_once_approves_single_execution(self):
        mgr = ApprovalManager()
        mgr.grant_once("submit")
        assert mgr.is_approved("submit") is True   # consumes
        assert mgr.is_approved("submit") is False  # consumed

    def test_grant_once_does_not_approve_other_actions(self):
        mgr = ApprovalManager()
        mgr.grant_once("submit")
        assert mgr.is_approved("navigate") is False

    def test_grant_unlimited_approves_multiple(self):
        mgr = ApprovalManager()
        mgr.grant_unlimited("submit")
        for _ in range(10):
            assert mgr.is_approved("submit") is True

    def test_grant_unlimited_with_glob(self):
        mgr = ApprovalManager()
        mgr.grant_unlimited("submit*")
        assert mgr.is_approved("submit") is True
        assert mgr.is_approved("submit_form") is True
        assert mgr.is_approved("navigate") is False  # doesn't match

    def test_revoke_grants_specific(self):
        mgr = ApprovalManager()
        mgr.grant_unlimited("submit")
        mgr.grant_unlimited("navigate")
        removed = mgr.revoke_grants("submit")
        assert removed == 1
        assert mgr.is_approved("submit") is False
        assert mgr.is_approved("navigate") is True

    def test_revoke_all_grants(self):
        mgr = ApprovalManager()
        mgr.grant_unlimited("submit")
        mgr.grant_unlimited("navigate")
        mgr.grant_unlimited("click")
        removed = mgr.revoke_grants()
        assert removed == 3
        assert mgr.is_approved("submit") is False

    def test_multiple_grant_once(self):
        mgr = ApprovalManager()
        mgr.grant_once("submit")
        mgr.grant_once("submit")
        assert mgr.is_approved("submit") is True   # first grant
        assert mgr.is_approved("submit") is True   # second grant
        assert mgr.is_approved("submit") is False  # both consumed


# ── Multiple pending requests ─────────────────────────────────────────────────

class TestMultiplePending:
    def test_multiple_requests_for_same_action(self):
        mgr = ApprovalManager()
        r1 = mgr.request("submit")
        r2 = mgr.request("submit")
        mgr.grant(r1)
        # First is_approved consumes r1
        assert mgr.is_approved("submit") is True
        # r2 still pending — not approved
        assert mgr.is_approved("submit") is False

    def test_only_approved_request_consumed(self):
        mgr = ApprovalManager()
        r1 = mgr.request("submit")  # pending, not approved
        r2 = mgr.request("submit")  # will be approved
        mgr.grant(r2)
        assert mgr.is_approved("submit") is True  # r2 consumed
        assert not mgr.has_pending("submit") or len(mgr.pending_for("submit")) == 1

    def test_pending_for_returns_correct_action(self):
        mgr = ApprovalManager()
        mgr.request("submit")
        mgr.request("navigate")
        assert len(mgr.pending_for("submit")) == 1
        assert len(mgr.pending_for("navigate")) == 1
        assert len(mgr.all_pending()) == 2


# ── Inspection / audit ────────────────────────────────────────────────────────

class TestInspection:
    def test_has_pending_false_when_empty(self):
        mgr = ApprovalManager()
        assert mgr.has_pending() is False

    def test_has_pending_true_after_request(self):
        mgr = ApprovalManager()
        mgr.request("submit")
        assert mgr.has_pending() is True
        assert mgr.has_pending("submit") is True

    def test_clear_pending(self):
        mgr = ApprovalManager()
        mgr.request("submit")
        mgr.request("navigate")
        mgr.clear_pending()
        assert not mgr.has_pending()

    def test_audit_log_after_grant(self):
        mgr = ApprovalManager()
        req_id = mgr.request("submit", {"url": "https://example.com"})
        mgr.grant(req_id)
        mgr.is_approved("submit")  # consume + move to resolved
        log = mgr.audit_log()
        assert len(log) == 1
        assert log[0]["action"] == "submit"
        assert log[0]["status"] == "approved"

    def test_audit_log_after_deny(self):
        mgr = ApprovalManager()
        req_id = mgr.request("navigate")
        mgr.deny(req_id)
        log = mgr.audit_log()
        assert len(log) == 1
        assert log[0]["status"] == "denied"

    def test_repr(self):
        mgr = ApprovalManager()
        mgr.request("submit")
        r = repr(mgr)
        assert "pending=1" in r
