"""
Scroll action — scroll the viewport or a specific element.

Mirrors Lightpanda scroll behavior:
    1. Resolve target (optional — None = window/document scroll)
    2. Compute new scroll position (absolute or relative delta)
    3. Apply to session scroll state
    4. Dispatch 'scroll' event via JS runtime (if available)
    5. Drain microtasks
    6. Return ActionResult with new scroll offset
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)

# Session attribute names for scroll state
_SCROLL_X_ATTR = "_scroll_x"
_SCROLL_Y_ATTR = "_scroll_y"


class ScrollAction(Action):
    """
    Scroll the page or an element.

    Parameters:
    - ``target``:    Optional CSS selector / node_id dict to scroll a specific element.
                     If omitted or None, scrolls the window/document.
    - ``delta_x``:   Horizontal scroll delta (pixels, default 0).
    - ``delta_y``:   Vertical scroll delta (pixels, default 300 = one viewport down).
    - ``to_top``:    If True, scroll to the very top (sets y=0).
    - ``to_bottom``: If True, scroll to end (best-effort with large value).
    - ``absolute``:  If True, treat delta_x/delta_y as absolute positions.

    Effects keys:
    - ``scroll_x``:      Final horizontal scroll position.
    - ``scroll_y``:      Final vertical scroll position.
    - ``delta_x``:       Horizontal delta applied.
    - ``delta_y``:       Vertical delta applied.
    - ``target_element``: Node ID if an element was scrolled, else None.
    - ``events_dispatched``: ["scroll"] if JS dispatch succeeded.
    """

    async def execute(
        self,
        session: "Session",
        target: Any = None,
        delta_x: int = 0,
        delta_y: int = 300,
        to_top: bool = False,
        to_bottom: bool = False,
        absolute: bool = False,
        **kwargs: Any,
    ) -> "ActionResult":
        from an_web.dom.semantics import ActionResult

        # ── 1. Resolve optional element target ────────────────────────
        element = None
        target_node_id = None
        if target:
            element = await self._resolve_target(target, session)
            if element is not None:
                target_node_id = getattr(element, "node_id", None)

        # ── 2. Compute new scroll position ───────────────────────────
        current_x = getattr(session, _SCROLL_X_ATTR, 0)
        current_y = getattr(session, _SCROLL_Y_ATTR, 0)

        if to_top:
            new_x = 0
            new_y = 0
        elif to_bottom:
            new_x = current_x
            new_y = current_y + 99_999  # large sentinel
        elif absolute:
            new_x = delta_x
            new_y = delta_y
        else:
            new_x = max(0, current_x + delta_x)
            new_y = max(0, current_y + delta_y)

        actual_dx = new_x - current_x
        actual_dy = new_y - current_y

        # ── 3. Apply to session state ─────────────────────────────────
        try:
            setattr(session, _SCROLL_X_ATTR, new_x)
            setattr(session, _SCROLL_Y_ATTR, new_y)
        except AttributeError:
            pass  # read-only session (tests)

        # ── 4. Dispatch 'scroll' event via JS ─────────────────────────
        events_dispatched = _dispatch_scroll_event(element, session, new_x, new_y)

        # ── 5. Drain event loop ───────────────────────────────────────
        if session.scheduler:
            await session.scheduler.drain_microtasks()

        return ActionResult(
            status="ok",
            action="scroll",
            target=target_node_id,
            effects={
                "scroll_x": new_x,
                "scroll_y": new_y,
                "delta_x": actual_dx,
                "delta_y": actual_dy,
                "target_element": target_node_id,
                "events_dispatched": events_dispatched,
            },
        )


# ─── Event dispatch helper ─────────────────────────────────────────────────────


def _dispatch_scroll_event(element: Any, session: Any, scroll_x: int, scroll_y: int) -> list[str]:
    """
    Dispatch a 'scroll' event on the target element or window.

    Also updates window.scrollX / window.scrollY in the JS context.
    """
    js_runtime = getattr(session, "js_runtime", None)
    if js_runtime is None or not js_runtime.is_available():
        return []

    if element is not None:
        from an_web.actions.click import _make_js_selector
        sel = _make_js_selector(element)
        if sel:
            js_code = (
                f"(function(){{"
                f"  var _el = {sel};"
                f"  if (_el) {{"
                f"    _el.scrollLeft = {scroll_x};"
                f"    _el.scrollTop = {scroll_y};"
                f"    _el.dispatchEvent(new Event('scroll', {{bubbles:false}}));"
                f"  }}"
                f"}})()"
            )
            js_runtime.eval_safe(js_code)
            return ["scroll"]
    else:
        # Window scroll
        js_code = (
            f"(function(){{"
            f"  try {{"
            f"    window.scrollX = {scroll_x}; window.scrollY = {scroll_y};"
            f"    window.pageXOffset = {scroll_x}; window.pageYOffset = {scroll_y};"
            f"  }} catch(e) {{}}"
            f"  window.dispatchEvent(new Event('scroll', {{bubbles:false}}));"
            f"}})()"
        )
        js_runtime.eval_safe(js_code)
        return ["scroll"]

    return []
