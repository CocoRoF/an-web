"""Click action — MouseEvent dispatch with full event loop flush."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class ClickAction(Action):
    """
    Click an element by dispatching MouseEvent.

    Pattern from Lightpanda actions.zig click():
        1. Resolve element
        2. Dispatch mousedown → mouseup → click events
        3. Drain microtasks
        4. Observe DOM mutations + navigation
        5. Return structured ActionResult
    """

    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        # 1. Resolve target
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure(
                "click", "target_not_found",
                target=str(target),
                recommended=[{"tool": "snapshot", "note": "check current page state"}],
            )

        # 2. Check visibility & interactability
        if hasattr(element, "visibility_state") and element.visibility_state == "none":
            return self._make_failure(
                "click", "target_not_visible",
                target=element.node_id,
                recommended=[],
            )

        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure(
                "click", "target_disabled",
                target=element.node_id,
            )

        # 3. Track pre-click state
        doc = getattr(session, "_current_document", None)
        pre_url = getattr(session, "_current_url", "")

        # 4. Dispatch click event
        _dispatch_click(element)

        # 5. Drain microtasks
        if session.scheduler:
            await session.scheduler.drain_microtasks()
            await session.scheduler.settle_network(timeout=3.0)
            await session.scheduler.flush_dom_mutations()

        # 6. Collect mutations
        from an_web.dom.mutation import MutationObserver
        mutations: list = []  # populated by MutationObserver in full impl

        post_url = getattr(session, "_current_url", "")
        navigated = post_url != pre_url

        return ActionResult(
            status="ok",
            action="click",
            target=element.node_id if hasattr(element, "node_id") else str(target),
            effects={
                "navigation": navigated,
                "final_url": post_url if navigated else None,
                "dom_mutations": len(mutations),
                "modal_opened": False,  # layout-lite will detect this
            },
        )


def _dispatch_click(element: Any) -> None:
    """
    Dispatch click event to element.
    In full implementation, this triggers the JS event system.
    For stub: mark element as clicked via attribute.
    """
    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_clicked", "true")
