"""Form submit action."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class SubmitAction(Action):
    async def execute(
        self, session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("submit", "target_not_found", target=str(target))

        pre_url = getattr(session, "_current_url", "")
        # Trigger form submission (stub — full impl dispatches submit event)
        if hasattr(element, "set_attribute"):
            element.set_attribute("_an_web_submitted", "true")

        if session.scheduler:
            await session.scheduler.drain_microtasks()
            await session.scheduler.settle_network(timeout=5.0)

        post_url = getattr(session, "_current_url", "")
        return ActionResult(
            status="ok",
            action="submit",
            target=getattr(element, "node_id", str(target)),
            effects={
                "form_submitted": True,
                "navigation": post_url != pre_url,
                "final_url": post_url,
            },
        )
