"""Action base class — precondition/execute/postcondition/artifact pattern."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class Action(ABC):
    """
    Base class for all AN-Web actions.

    Pattern:
        1. precondition  — verify action can be performed
        2. execute       — perform the DOM/event operation
        3. event_flush   — drain microtasks + network
        4. postcondition — observe state changes
        5. artifact      — collect evidence for replay/debug
    """

    @abstractmethod
    async def execute(self, session: Session, **kwargs: Any) -> ActionResult:
        ...

    async def _resolve_target(
        self,
        target: str | dict[str, Any],
        session: Session,
    ) -> Any:
        """Resolve a target (selector, node_id, or semantic query) to a DOM node."""
        if isinstance(target, str):
            # CSS selector or XPath
            from an_web.dom.document import query_selector
            if not hasattr(session, "_current_document") or session._current_document is None:
                return None
            return query_selector(session._current_document, target)

        if isinstance(target, dict):
            by = target.get("by", "selector")
            if by == "node_id":
                return self._resolve_by_node_id(target.get("node_id", ""), session)
            if by in ("semantic", "role", "text"):
                return await self._resolve_semantic(target, session)

        return None

    def _resolve_by_node_id(self, node_id: str, session: Session) -> Any:
        if not hasattr(session, "_current_document") or session._current_document is None:
            return None
        for el in session._current_document.iter_elements():
            if el.node_id == node_id:
                return el
        return None

    async def _resolve_semantic(
        self, target: dict[str, Any], session: Session
    ) -> Any:
        """Resolve semantic target (role+text combination)."""
        from an_web.semantic.extractor import SemanticExtractor
        extractor = SemanticExtractor()
        semantics = await extractor.extract(session=session)
        tree = semantics.semantic_tree

        by = target.get("by")
        if by == "role":
            role = target.get("role", "")
            candidates = tree.find_by_role(role)
            if candidates:
                text = target.get("text")
                if text:
                    for c in candidates:
                        if c.name and text.lower() in c.name.lower():
                            return self._resolve_by_node_id(c.node_id, session)
                return self._resolve_by_node_id(candidates[0].node_id, session)
        elif by in ("text", "semantic"):
            text = target.get("text", "")
            candidates = tree.find_by_text(text)
            if candidates:
                # find_by_text matches partially, so "new" also matches the
                # page root named "Hacker News".  Prefer exact-text matches,
                # then interactive elements, then the tightest name.
                query = text.strip().lower()

                def _rank(c: Any) -> tuple[int, int, int]:
                    name = (c.name or "").strip().lower()
                    return (
                        0 if name == query else 1,
                        0 if getattr(c, "is_interactive", False) else 1,
                        len(name),
                    )

                best = min(candidates, key=_rank)
                return self._resolve_by_node_id(best.node_id, session)

        return None

    async def _settle_after_action(
        self, session: Session, budget_s: float = 5.0
    ) -> int:
        """
        Settle async work triggered by an action's event handlers.

        Mirrors real-browser behaviour (and playwright-mcp's
        waitForCompletion): fetch/XHR requests started by click/input
        handlers are performed and their Promises resolved, timers fire,
        and dynamically inserted scripts load — all within *budget_s*.

        Returns the number of async work items processed.
        """
        import asyncio as _asyncio
        import time as _time

        from an_web.actions.navigate import (
            _process_dynamic_scripts,
            _process_pending_fetches,
        )

        js_runtime = getattr(session, "js_runtime", None)
        deadline = _time.monotonic() + budget_s
        total = 0
        while _time.monotonic() < deadline:
            activity = 0
            if js_runtime is not None and js_runtime.is_available():
                activity += await js_runtime.drain_microtasks()
            activity += await _process_pending_fetches(session)
            activity += await _process_dynamic_scripts(session)
            if not activity:
                break
            total += activity
            await _asyncio.sleep(0.01)
        return total

    def _check_policy(
        self,
        session: Session,
        action_name: str,
        url: str | None = None,
        consume_resources: bool = True,
        details: dict[str, Any] | None = None,
    ) -> ActionResult | None:
        """
        Run full policy check via PolicyChecker.

        Returns:
            None                  — check passed; action may proceed.
            ActionResult(failed)  — blocked; return this immediately.
        """
        from an_web.policy.checker import PolicyChecker
        checker = PolicyChecker.for_session(session)
        result = checker.check_action(
            action_name,
            url=url,
            consume_resources=consume_resources,
            details=details,
        )
        if result.blocked:
            error = result.reason or f"policy_violation: {result.violation_type}"
            extra: dict[str, Any] = {}
            if result.approval_id:
                extra["approval_id"] = result.approval_id
            return self._make_failure(
                action_name,
                error=error,
                target=url,
                recommended=[{"note": "check policy", **extra}],
            )
        return None

    def _make_failure(
        self,
        action: str,
        error: str,
        target: str | None = None,
        recommended: list[dict[str, Any]] | None = None,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        return ActionResult(
            status="failed",
            action=action,
            target=target,
            error=error,
            recommended_next_actions=recommended or [],
        )
