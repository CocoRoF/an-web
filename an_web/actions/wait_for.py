"""Wait-for action — wait until a condition is satisfied."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class WaitForAction(Action):
    async def execute(
        self, session: Session,
        condition: str = "network_idle",
        selector: str | None = None,
        timeout_ms: int = 5000,
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        try:
            await asyncio.wait_for(
                self._wait(condition, selector, session),
                timeout=timeout_ms / 1000,
            )
            return ActionResult(
                status="ok", action="wait_for",
                effects={"condition": condition, "satisfied": True},
            )
        except TimeoutError:
            return ActionResult(
                status="failed", action="wait_for",
                error="timeout",
                effects={"condition": condition, "satisfied": False},
                recommended_next_actions=[
                    {"tool": "snapshot", "note": "Check current page state after timeout"}
                ],
            )

    async def _wait(self, condition: str, selector: str | None, session: Session) -> None:
        if condition == "network_idle":
            if session.scheduler:
                await session.scheduler.settle_network(timeout=30.0)
        elif condition == "dom_stable":
            await asyncio.sleep(0.5)  # stub: real impl watches mutation count
        elif condition == "element_visible" and selector:
            for _ in range(50):  # poll up to 5s
                doc = getattr(session, "_current_document", None)
                if doc:
                    from an_web.dom.document import query_selector
                    el = query_selector(doc, selector)
                    if el and getattr(el, "visibility_state", "visible") == "visible":
                        return
                await asyncio.sleep(0.1)
            raise TimeoutError
