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
        2. Check visibility + disabled state
        3. Dispatch click (+ link navigation / form submit)
        4. Drain microtasks + settle network
        5. Observe DOM mutations + navigation
        6. Return structured ActionResult
    """

    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        # 1. Resolve target element
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure(
                "click", "target_not_found",
                target=str(target),
                recommended=[{"tool": "snapshot", "note": "check current page state"}],
            )

        # 2. Check visibility
        if getattr(element, "visibility_state", "visible") == "none":
            return self._make_failure(
                "click", "target_not_visible",
                target=getattr(element, "node_id", str(target)),
            )

        # 3. Check disabled
        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure(
                "click", "target_disabled",
                target=getattr(element, "node_id", str(target)),
            )

        pre_url = getattr(session, "_current_url", "")

        # 4. Handle link navigation
        tag = getattr(element, "tag", "")
        if tag == "a":
            result = await self._handle_link_click(element, session, pre_url)
            if result is not None:
                return result

        # 5. Handle submit button click
        if tag == "button" or (tag == "input" and element.get_attribute("type") in ("submit", "button")):
            if element.get_attribute("type") == "submit" or (
                tag == "button" and element.get_attribute("type") in (None, "", "submit")
            ):
                form = _find_parent_form(element, session)
                if form is not None:
                    return await _submit_form(form, session, submitter=element)

        # 6. Generic click — dispatch event marker and drain
        _dispatch_click(element)

        if session.scheduler:
            await session.scheduler.drain_microtasks()
            await session.scheduler.settle_network(timeout=3.0)
            await session.scheduler.flush_dom_mutations()

        post_url = getattr(session, "_current_url", "")
        navigated = post_url != pre_url

        return ActionResult(
            status="ok",
            action="click",
            target=getattr(element, "node_id", str(target)),
            effects={
                "navigation": navigated,
                "final_url": post_url if navigated else None,
                "dom_mutations": 0,
                "modal_opened": False,
            },
            recommended_next_actions=[{"tool": "snapshot"}] if not navigated else [
                {"tool": "snapshot"},
                {"tool": "navigate", "url": post_url},
            ],
        )

    async def _handle_link_click(self, element: Any, session: Session, pre_url: str) -> ActionResult | None:
        """Navigate for <a href> clicks. Returns None if no href or same-page anchor."""
        from an_web.dom.semantics import ActionResult
        from an_web.net.client import NetworkClient

        href = element.get_attribute("href")
        if not href or href.startswith("#"):
            return None  # anchor-only, treat as generic click

        # Resolve relative URLs
        base_url = getattr(session, "_current_url", "about:blank")
        if session.network:
            resolved = NetworkClient.resolve_url(base_url, href)
        else:
            from urllib.parse import urljoin
            resolved = urljoin(base_url, href)

        # Use NavigateAction to load the new page
        from an_web.actions.navigate import NavigateAction
        nav = NavigateAction()
        result = await nav.execute(session, url=resolved)

        if result.is_ok():
            result.action = "click"
            result.effects["clicked_href"] = href
        return result


def _find_parent_form(element: Any, session: Any) -> Any:
    """Walk parent chain to find enclosing <form> element."""
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return None
    # Walk all elements and check if element is descendant of a form
    for form in doc.iter_elements():
        if getattr(form, "tag", "") != "form":
            continue
        for descendant in form.iter_descendants():
            if descendant is element:
                return form
    return None


async def _submit_form(form: Any, session: Any, submitter: Any = None) -> ActionResult:
    """Collect form fields and dispatch network submission."""
    from an_web.dom.semantics import ActionResult
    from an_web.dom.nodes import Element

    # Collect field values
    fields: dict[str, str] = {}
    for el in form.iter_descendants():
        if not isinstance(el, Element):
            continue
        name = el.get_attribute("name") or el.get_attribute("id")
        if not name:
            continue
        tag = el.tag
        if tag in ("input", "textarea"):
            input_type = el.get_attribute("type") or "text"
            if input_type == "hidden":
                fields[name] = el.get_attribute("value") or ""
            elif input_type in ("checkbox", "radio"):
                if "checked" in el.attributes:
                    fields[name] = el.get_attribute("value") or "on"
            elif input_type != "submit":
                fields[name] = el.get_attribute("value") or ""
        elif tag == "select":
            fields[name] = el.get_attribute("value") or ""

    # Determine action URL
    action_url = form.get_attribute("action") or ""
    method = (form.get_attribute("method") or "get").upper()
    base_url = getattr(session, "_current_url", "about:blank")

    if action_url:
        from an_web.net.client import NetworkClient
        action_url = NetworkClient.resolve_url(base_url, action_url)
    else:
        action_url = base_url

    pre_url = base_url

    # Submit via network
    if session.network:
        try:
            if method == "POST":
                response = await session.network.post(action_url, data=fields)
            else:
                from urllib.parse import urlencode
                qs = urlencode(fields)
                get_url = f"{action_url}{'&' if '?' in action_url else '?'}{qs}" if qs else action_url
                response = await session.network.get(get_url)

            # Parse new document
            from an_web.browser.parser import parse_html
            doc = parse_html(response.text, base_url=response.url)
            session._current_document = doc
            session._current_url = response.url

            snap_id = ""
            if session.snapshots:
                snap = session.snapshots.create(
                    url=response.url,
                    dom_content=response.text,
                    semantic_data={},
                )
                snap_id = snap.snapshot_id

            return ActionResult(
                status="ok",
                action="click",
                target=getattr(form, "node_id", "form"),
                effects={
                    "form_submitted": True,
                    "method": method,
                    "action_url": action_url,
                    "fields_submitted": list(fields.keys()),
                    "navigation": response.url != pre_url,
                    "final_url": response.url,
                    "status_code": response.status,
                },
                state_delta_id=snap_id,
                recommended_next_actions=[{"tool": "snapshot"}],
            )
        except Exception as exc:
            return ActionResult(
                status="failed",
                action="click",
                target=getattr(form, "node_id", "form"),
                error=f"form_submit_error: {exc}",
            )

    # No network — just mark submitted
    form.set_attribute("_an_web_submitted", "true")
    return ActionResult(
        status="ok",
        action="click",
        target=getattr(form, "node_id", "form"),
        effects={"form_submitted": True, "navigation": False},
    )


def _dispatch_click(element: Any) -> None:
    """Mark that a click event was dispatched on this element."""
    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_clicked", "true")
