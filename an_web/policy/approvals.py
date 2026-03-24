"""Destructive action approval management."""
from __future__ import annotations
from typing import Any


class ApprovalManager:
    """Manage approval requirements for sensitive actions."""

    def __init__(self) -> None:
        self._pending: list[dict[str, Any]] = []
        self._auto_approve: bool = False

    def request_approval(self, action: str, details: dict[str, Any]) -> str:
        """Queue an approval request, return request ID."""
        import uuid
        req_id = str(uuid.uuid4())[:8]
        self._pending.append({"id": req_id, "action": action, "details": details})
        return req_id

    def approve(self, request_id: str) -> bool:
        self._pending = [p for p in self._pending if p["id"] != request_id]
        return True

    def deny(self, request_id: str) -> bool:
        self._pending = [p for p in self._pending if p["id"] != request_id]
        return False

    def set_auto_approve(self, value: bool) -> None:
        self._auto_approve = value
