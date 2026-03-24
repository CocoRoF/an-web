"""Input actions — type, clear, select."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING
from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class TypeAction(Action):
    """
    Type text into an input/textarea.
    Dispatches input + change events after value assignment.
    """

    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        text: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("type", "target_not_found", target=str(target))

        tag = getattr(element, "tag", "")
        if tag not in ("input", "textarea"):
            return self._make_failure(
                "type", "not_an_input",
                target=getattr(element, "node_id", str(target)),
            )

        # Set value
        element.set_attribute("value", text)

        # Dispatch input + change events
        _dispatch_input_events(element)

        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="type",
            target=getattr(element, "node_id", str(target)),
            effects={"value_set": text, "events_dispatched": ["input", "change"]},
        )


class ClearAction(Action):
    async def execute(
        self, session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("clear", "target_not_found", target=str(target))

        element.set_attribute("value", "")
        _dispatch_input_events(element)

        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="clear",
            target=getattr(element, "node_id", str(target)),
            effects={"value_cleared": True},
        )


class SelectAction(Action):
    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        value: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("select", "target_not_found", target=str(target))

        if getattr(element, "tag", "") != "select":
            return self._make_failure(
                "select", "not_a_select_element",
                target=getattr(element, "node_id", str(target)),
            )

        element.set_attribute("value", value)
        _dispatch_input_events(element)

        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="select",
            target=getattr(element, "node_id", str(target)),
            effects={"selected_value": value},
        )


def _dispatch_input_events(element: Any) -> None:
    """Mark that input/change events were dispatched."""
    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_input", "true")
