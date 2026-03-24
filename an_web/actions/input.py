"""
Input actions — type, clear, select.

Pattern from Lightpanda actions.zig:
    1. Resolve element
    2. Precondition: check tag + disabled state
    3. Set DOM value attribute
    4. Dispatch InputEvent → change event (via JS runtime if available)
    5. Drain microtasks
    6. Return ActionResult with value_set + events_dispatched
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)

_TYPEABLE_TAGS = ("input", "textarea")
_UNTYPEABLE_INPUT_TYPES = ("submit", "reset", "button", "image", "file", "hidden")


class TypeAction(Action):
    """
    Type text into an input, textarea, or contenteditable element.

    Accepted ``target`` formats: CSS selector, node_id dict, semantic dict.

    Parameters:
    - ``text``:   Text to type.
    - ``append``: If True, append to current value instead of replacing.

    Effects keys:
    - ``value_set``:          Final value in the element.
    - ``events_dispatched``:  List of event names dispatched (input, change).
    - ``appended``:           True if append mode was used.
    """

    async def execute(
        self,
        session: "Session",
        target: str | dict[str, Any] = "",
        text: str = "",
        append: bool = False,
        **kwargs: Any,
    ) -> "ActionResult":
        from an_web.dom.semantics import ActionResult

        # ── 1. Resolve target ─────────────────────────────────────────
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("type", "target_not_found", target=str(target))

        node_id = getattr(element, "node_id", str(target))
        tag = getattr(element, "tag", "")

        # ── 2. Preconditions ──────────────────────────────────────────
        # contenteditable support
        is_contenteditable = (
            element.get_attribute("contenteditable") in ("true", "")
            if hasattr(element, "get_attribute")
            else False
        )

        if tag not in _TYPEABLE_TAGS and not is_contenteditable:
            return self._make_failure(
                "type",
                "not_an_input",
                target=node_id,
                recommended=[
                    {"tool": "snapshot", "note": "verify target is a text input"},
                    {"tool": "extract", "query": "input, textarea, [contenteditable]"},
                ],
            )

        # Reject non-text input types
        if tag == "input":
            inp_type = (element.get_attribute("type") or "text").lower()
            if inp_type in _UNTYPEABLE_INPUT_TYPES:
                return self._make_failure(
                    "type",
                    f"not_typeable_input_type:{inp_type}",
                    target=node_id,
                )

        # Disabled check
        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure(
                "type",
                "target_disabled",
                target=node_id,
                recommended=[{"tool": "snapshot"}],
            )
        # Also check disabled attribute
        if hasattr(element, "get_attribute") and element.get_attribute("disabled") is not None:
            return self._make_failure("type", "target_disabled", target=node_id)

        # ── 3. Set DOM value ──────────────────────────────────────────
        if is_contenteditable:
            # contenteditable — update text_content (best-effort)
            current = getattr(element, "text_content", "") or ""
            new_value = (current + text) if append else text
            if hasattr(element, "set_attribute"):
                element.set_attribute("_an_web_text_content", new_value)
        else:
            current = element.get_attribute("value") or "" if hasattr(element, "get_attribute") else ""
            new_value = (current + text) if append else text
            element.set_attribute("value", new_value)

        # ── 4. Dispatch events ────────────────────────────────────────
        events_dispatched = _dispatch_input_events(element, session, new_value)

        # ── 5. Drain event loop ───────────────────────────────────────
        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="type",
            target=node_id,
            effects={
                "value_set": new_value,
                "events_dispatched": events_dispatched,
                "appended": append,
            },
            recommended_next_actions=[{"tool": "snapshot"}],
        )


class ClearAction(Action):
    """
    Clear the value of an input or textarea.

    Effects keys:
    - ``value_cleared``:      Always True on success.
    - ``previous_value``:     The value before clearing.
    - ``events_dispatched``:  Events fired (input, change).
    """

    async def execute(
        self,
        session: "Session",
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> "ActionResult":
        from an_web.dom.semantics import ActionResult

        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("clear", "target_not_found", target=str(target))

        node_id = getattr(element, "node_id", str(target))
        tag = getattr(element, "tag", "")

        if tag not in _TYPEABLE_TAGS:
            return self._make_failure("clear", "not_an_input", target=node_id)

        # Disabled check
        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure("clear", "target_disabled", target=node_id)
        if hasattr(element, "get_attribute") and element.get_attribute("disabled") is not None:
            return self._make_failure("clear", "target_disabled", target=node_id)

        previous = element.get_attribute("value") or "" if hasattr(element, "get_attribute") else ""
        element.set_attribute("value", "")
        events_dispatched = _dispatch_input_events(element, session, "")

        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="clear",
            target=node_id,
            effects={
                "value_cleared": True,
                "previous_value": previous,
                "events_dispatched": events_dispatched,
            },
        )


class SelectAction(Action):
    """
    Select an option in a <select> element.

    Parameters:
    - ``value``:  The option value to select.
    - ``by_text``: If True, match by option visible text instead of value.

    Effects keys:
    - ``selected_value``:     The value attribute set on the element.
    - ``events_dispatched``:  Events fired (change).
    - ``option_found``:       Whether the option exists in the DOM.
    """

    async def execute(
        self,
        session: "Session",
        target: str | dict[str, Any] = "",
        value: str = "",
        by_text: bool = False,
        **kwargs: Any,
    ) -> "ActionResult":
        from an_web.dom.semantics import ActionResult

        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure("select", "target_not_found", target=str(target))

        node_id = getattr(element, "node_id", str(target))

        if getattr(element, "tag", "") != "select":
            return self._make_failure(
                "select",
                "not_a_select_element",
                target=node_id,
                recommended=[
                    {"tool": "extract", "query": "select"},
                ],
            )

        # Disabled check
        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure("select", "target_disabled", target=node_id)
        if hasattr(element, "get_attribute") and element.get_attribute("disabled") is not None:
            return self._make_failure("select", "target_disabled", target=node_id)

        # ── Validate option existence + resolve by_text ───────────────
        actual_value = value
        option_found = False
        for child in _iter_option_elements(element):
            opt_val = child.get_attribute("value") or "" if hasattr(child, "get_attribute") else ""
            opt_text = getattr(child, "text_content", "").strip()
            if by_text:
                if opt_text == value:
                    actual_value = opt_val or opt_text
                    option_found = True
                    break
            else:
                if opt_val == value:
                    option_found = True
                    break

        if not option_found and value:
            log.debug("SelectAction: option %r not found — setting anyway", value)

        element.set_attribute("value", actual_value)
        # Mark selected option
        for child in _iter_option_elements(element):
            opt_val = child.get_attribute("value") or "" if hasattr(child, "get_attribute") else ""
            if opt_val == actual_value:
                if hasattr(child, "set_attribute"):
                    child.set_attribute("selected", "")
            else:
                if hasattr(child, "remove_attribute"):
                    child.remove_attribute("selected")

        events_dispatched = _dispatch_change_event(element, session)

        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="select",
            target=node_id,
            effects={
                "selected_value": actual_value,
                "events_dispatched": events_dispatched,
                "option_found": option_found,
            },
        )


# ─── Event dispatch helpers ────────────────────────────────────────────────────


def _dispatch_input_events(element: Any, session: Any, new_value: str) -> list[str]:
    """
    Dispatch 'input' then 'change' events on the element.

    Uses JS runtime (InputEvent + Event) if available, else attribute markers.
    """
    js_runtime = getattr(session, "js_runtime", None)

    if js_runtime is not None and js_runtime.is_available():
        from an_web.actions.click import _make_js_selector
        selector = _make_js_selector(element)
        if selector:
            safe_val = new_value.replace("\\", "\\\\").replace("'", "\\'")
            js_code = (
                f"(function(){{"
                f"  var _el = {selector};"
                f"  if (_el) {{"
                f"    var nativeInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');"
                f"    if (nativeInput && nativeInput.set) {{ nativeInput.set.call(_el, '{safe_val}'); }}"
                f"    _el.dispatchEvent(new InputEvent('input', {{bubbles:true, cancelable:true}}));"
                f"    _el.dispatchEvent(new Event('change', {{bubbles:true}}));"
                f"  }}"
                f"}})()"
            )
            js_runtime.eval_safe(js_code)
            return ["input", "change"]

    # Fallback: attribute markers
    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_input", "true")
    return ["input", "change"]


def _dispatch_change_event(element: Any, session: Any) -> list[str]:
    """Dispatch 'change' event on a select element."""
    js_runtime = getattr(session, "js_runtime", None)

    if js_runtime is not None and js_runtime.is_available():
        from an_web.actions.click import _make_js_selector
        selector = _make_js_selector(element)
        if selector:
            js_code = (
                f"(function(){{"
                f"  var _el = {selector};"
                f"  if (_el) _el.dispatchEvent(new Event('change', {{bubbles:true}}));"
                f"}})()"
            )
            js_runtime.eval_safe(js_code)
            return ["change"]

    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_change", "true")
    return ["change"]


def _iter_option_elements(select_element: Any):
    """Yield <option> children of a <select> element."""
    if not hasattr(select_element, "iter_descendants"):
        return
    for child in select_element.iter_descendants():
        if getattr(child, "tag", "") == "option":
            yield child
