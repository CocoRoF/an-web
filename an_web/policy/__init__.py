"""
Policy and safety layer for AN-Web.

AI browser systems are action systems — safety policy must be
enforced at the core level BEFORE action execution, not as post-processing.

Modules:
    rules      - Domain allow/deny, rate limiting, navigation scope
    sandbox    - Execution isolation per session
    approvals  - Destructive action confirmation flags
    checker    - Unified PolicyChecker combining all three modules
"""
from an_web.policy.rules import (
    PolicyRules,
    PolicyCheckResult,
    NavigationScope,
    ViolationType,
)
from an_web.policy.sandbox import Sandbox, SandboxLimits, Resource
from an_web.policy.approvals import ApprovalManager, PendingApproval
from an_web.policy.checker import PolicyChecker

__all__ = [
    "PolicyRules",
    "PolicyCheckResult",
    "NavigationScope",
    "ViolationType",
    "Sandbox",
    "SandboxLimits",
    "Resource",
    "ApprovalManager",
    "PendingApproval",
    "PolicyChecker",
]
