"""Scroll action."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class ScrollAction(Action):
    async def execute(
        self, session: Session,
        target: Any = None,
        delta_x: int = 0,
        delta_y: int = 300,
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        return ActionResult(
            status="ok",
            action="scroll",
            effects={"delta_x": delta_x, "delta_y": delta_y},
        )
