"""
Destructive action approval management for AN-Web.

Approval flow:
  1. PolicyRules.requires_approval("submit") → True
  2. PolicyChecker.check_action("submit") → PolicyCheckResult(blocked, APPROVAL_REQUIRED)
  3. Caller calls ApprovalManager.request("submit", details) → approval_id
  4. Human (or test harness) calls ApprovalManager.grant(approval_id)
  5. PolicyChecker re-checks → now allowed (approval consumed one-time)

Auto-approve modes:
  - set_auto_approve(True)     — blanket auto-approval (useful for tests)
  - grant_once("submit")       — pre-approve a single execution of one action
  - grant_pattern("submit.*")  — pre-approve all matching action names
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any


# ── Pending approval record ───────────────────────────────────────────────────

@dataclass
class PendingApproval:
    """
    An outstanding approval request.

    States: pending → (approved | denied | expired)
    """
    request_id:   str
    action:       str
    details:      dict[str, Any]
    requested_at: float = field(default_factory=time.monotonic)
    expires_at:   float = field(default=0.0)   # 0 = no expiry
    status:       str = "pending"              # pending | approved | denied | expired

    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        return time.monotonic() > self.expires_at

    def is_pending(self) -> bool:
        if self.is_expired():
            self.status = "expired"
            return False
        return self.status == "pending"


# ── One-time approval grants ──────────────────────────────────────────────────

@dataclass
class ApprovalGrant:
    """
    A pre-approved grant for an action pattern.

    ``uses_remaining`` — how many times it can be consumed.
    Use 0 for unlimited (until revoked).
    """
    pattern:       str    # exact action name or glob, e.g. "submit" or "submit*"
    uses_remaining: int   # 0 = unlimited
    granted_by:    str    # who/what granted this


# ── Approval manager ──────────────────────────────────────────────────────────

class ApprovalManager:
    """
    Manage approval requirements for sensitive / destructive actions.

    Thread-safety: NOT thread-safe. All calls expected on the event loop.

    Usage::

        mgr = ApprovalManager()
        req_id = mgr.request("submit", {"url": "https://...", "form": "..."})

        # In tests or interactive flow:
        mgr.grant(req_id)

        # Check (consumes the grant):
        assert mgr.is_approved("submit")

        # Auto-approve everything for testing:
        mgr.set_auto_approve(True)
    """

    def __init__(self, auto_approve: bool = False) -> None:
        self._auto_approve: bool = auto_approve

        # Outstanding requests awaiting human decision
        self._pending: dict[str, PendingApproval] = {}

        # Resolved (granted / denied) — kept for audit trail
        self._resolved: list[PendingApproval] = []

        # One-time / unlimited pre-approval grants
        self._grants: list[ApprovalGrant] = []

    # ── Request ───────────────────────────────────────────────────────────────

    def request(
        self,
        action: str,
        details: dict[str, Any] | None = None,
        expires_in: float = 0.0,   # seconds; 0 = no expiry
    ) -> str:
        """
        Queue an approval request.

        Returns the request ID that the approver should pass to ``grant()``.
        If auto_approve is enabled, the request is immediately granted.
        """
        req_id = uuid.uuid4().hex[:12]
        expires_at = (time.monotonic() + expires_in) if expires_in > 0 else 0.0

        record = PendingApproval(
            request_id=req_id,
            action=action,
            details=details or {},
            expires_at=expires_at,
        )
        self._pending[req_id] = record

        if self._auto_approve:
            record.status = "approved"

        return req_id

    # Backward-compatible alias
    def request_approval(
        self,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        return self.request(action, details)

    # ── Grant / deny ──────────────────────────────────────────────────────────

    def grant(self, request_id: str) -> bool:
        """Approve a pending request by ID. Returns True if found."""
        record = self._pending.get(request_id)
        if record is None:
            return False
        record.status = "approved"
        return True

    def deny(self, request_id: str) -> bool:
        """Deny a pending request by ID. Returns True if found."""
        record = self._pending.get(request_id)
        if record is None:
            return False
        record.status = "denied"
        self._resolved.append(self._pending.pop(request_id))
        return True

    # Backward-compatible alias
    def approve(self, request_id: str) -> bool:
        return self.grant(request_id)

    # ── Consumption (one-time check) ──────────────────────────────────────────

    def is_approved(self, action: str) -> bool:
        """
        Check whether ``action`` has an approved pending request.

        Consumes the oldest approved request for this action (one-time use).
        Also checks pre-approval grants.

        Returns True if approved (and consumes the approval).
        """
        if self._auto_approve:
            return True

        # Check pre-approval grants first (non-destructive check first)
        if self._consume_grant(action):
            return True

        # Find the oldest approved pending request for this action
        for req_id, record in list(self._pending.items()):
            if record.action == action and record.status == "approved":
                if not record.is_expired():
                    self._resolved.append(self._pending.pop(req_id))
                    return True
                else:
                    # Expired — discard
                    record.status = "expired"
                    self._resolved.append(self._pending.pop(req_id))

        return False

    # ── Pre-approval grants ───────────────────────────────────────────────────

    def grant_once(self, action: str, granted_by: str = "system") -> None:
        """
        Pre-approve exactly one execution of ``action``.

        Useful in tests: call this before an action that requires approval.
        """
        self._grants.append(ApprovalGrant(
            pattern=action,
            uses_remaining=1,
            granted_by=granted_by,
        ))

    def grant_unlimited(self, pattern: str, granted_by: str = "system") -> None:
        """
        Pre-approve unlimited executions matching ``pattern`` (glob syntax).

        e.g. ``grant_unlimited("submit*")`` approves all submit variants.
        """
        self._grants.append(ApprovalGrant(
            pattern=pattern,
            uses_remaining=0,
            granted_by=granted_by,
        ))

    def revoke_grants(self, pattern: str | None = None) -> int:
        """
        Remove pre-approval grants.

        If ``pattern`` is given, removes only matching grants.
        Otherwise removes all grants.
        Returns count of removed grants.
        """
        before = len(self._grants)
        if pattern is None:
            self._grants.clear()
        else:
            self._grants = [g for g in self._grants if g.pattern != pattern]
        return before - len(self._grants)

    def _consume_grant(self, action: str) -> bool:
        """Try to consume one grant for ``action``. Returns True if consumed."""
        for i, grant in enumerate(self._grants):
            if grant.pattern == action or fnmatch(action, grant.pattern):
                if grant.uses_remaining == 0:
                    return True   # unlimited
                if grant.uses_remaining > 0:
                    grant.uses_remaining -= 1
                    if grant.uses_remaining == 0:
                        self._grants.pop(i)
                    return True
        return False

    # ── Auto-approve ──────────────────────────────────────────────────────────

    def set_auto_approve(self, value: bool) -> None:
        """Enable / disable blanket auto-approval (for testing)."""
        self._auto_approve = value

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    # ── Inspection ────────────────────────────────────────────────────────────

    def pending_for(self, action: str) -> list[PendingApproval]:
        """Return all pending (non-expired) requests for ``action``."""
        return [
            r for r in self._pending.values()
            if r.action == action and r.is_pending()
        ]

    def all_pending(self) -> list[PendingApproval]:
        """Return all outstanding pending requests."""
        return [r for r in self._pending.values() if r.is_pending()]

    def has_pending(self, action: str | None = None) -> bool:
        """Return True if there are pending requests (optionally filtered by action)."""
        if action is not None:
            return bool(self.pending_for(action))
        return bool(self.all_pending())

    def clear_pending(self) -> None:
        """Clear all pending requests (without resolving — use in tests)."""
        self._pending.clear()

    def audit_log(self) -> list[dict[str, Any]]:
        """Return resolved decisions for audit."""
        return [
            {
                "request_id": r.request_id,
                "action":     r.action,
                "status":     r.status,
                "details":    r.details,
            }
            for r in self._resolved
        ]

    def __repr__(self) -> str:
        pending = sum(1 for r in self._pending.values() if r.is_pending())
        grants  = len(self._grants)
        auto    = " auto=ON" if self._auto_approve else ""
        return f"ApprovalManager(pending={pending}, grants={grants}{auto})"
