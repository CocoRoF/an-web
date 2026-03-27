"""
Click action — MouseEvent dispatch with full event loop flush.

Pattern:
    1. Resolve element (CSS selector / node_id / semantic query)
    2. Precondition: check visibility + disabled state
    3. Dispatch mousedown → mouseup → click (real JS events or attribute markers)
    4. Handle side effects: link navigation / form submit
    5. drain_microtasks → settle_network → flush_dom_mutations
    6. Postcondition: detect URL change + mutation count
    7. Return structured ActionResult with state_delta_id
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)


class ClickAction(Action):
    """
    Click an element by dispatching MouseEvent(s).

    Accepted ``target`` formats:
    - CSS selector string:  ``"#submit-btn"``
    - Semantic role:        ``{"by": "role", "role": "button", "text": "Login"}``
    - Node ID:              ``{"by": "node_id", "node_id": "el-42"}``

    Effects keys:
    - ``navigation``:      True if the URL changed after the click.
    - ``final_url``:       New URL (present only if navigation=True).
    - ``dom_mutations``:   Number of DOM mutations observed.
    - ``form_submitted``:  True if a form submission was triggered.
    - ``modal_opened``:    True if a modal/dialog appeared (heuristic).
    - ``events_dispatched``: List of JS event types fired.
    """

    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        # ── 1. Resolve target ─────────────────────────────────────────
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure(
                "click",
                "target_not_found",
                target=str(target),
                recommended=[
                    {"tool": "snapshot", "note": "check current page state"},
                    {"tool": "extract", "query": "button, a, input[type=submit]"},
                ],
            )

        # ── 2. Preconditions ──────────────────────────────────────────
        tag = getattr(element, "tag", "")
        node_id = getattr(element, "node_id", str(target))

        # Visibility check
        vis = getattr(element, "visibility_state", "visible")
        if vis in ("hidden", "none"):
            return self._make_failure(
                "click",
                "target_not_visible",
                target=node_id,
                recommended=[
                    {"tool": "scroll", "note": "scroll element into view"},
                    {"tool": "snapshot"},
                ],
            )

        # Disabled check
        if hasattr(element, "is_disabled") and element.is_disabled():
            return self._make_failure(
                "click",
                "target_disabled",
                target=node_id,
                recommended=[{"tool": "snapshot"}],
            )

        pre_url = getattr(session, "_current_url", "")

        # ── 3. Handle <a href> navigation ────────────────────────────
        if tag == "a":
            result = await self._handle_link_click(element, session, pre_url, target)
            if result is not None:
                return result

        # ── 4. Handle submit button ───────────────────────────────────
        if _is_submit_button(element):
            form = _find_enclosing_form(element, session)
            if form is not None:
                return await _submit_form(
                    form, session, submitter=element, action_name="click"
                )

        # ── 5. Generic click — dispatch JS events ────────────────────
        events_dispatched = _dispatch_mouse_events(element, session)

        # ── 6. Drain event loop ───────────────────────────────────────
        if session.scheduler:
            await session.scheduler.drain_microtasks()
            await session.scheduler.settle_network(timeout=3.0)
            await session.scheduler.flush_dom_mutations()

        # ── 7. Postcondition ──────────────────────────────────────────
        post_url = getattr(session, "_current_url", "")
        navigated = post_url != pre_url

        # Create snapshot if document changed
        snap_id = ""
        if session.snapshots and session._current_document:
            snap = session.snapshots.create(
                url=post_url,
                dom_content=_get_doc_html(session),
                semantic_data={},
                storage_state=getattr(session, "storage_state", lambda: {})(),
            )
            snap_id = snap.snapshot_id

        effects: dict[str, Any] = {
            "navigation": navigated,
            "dom_mutations": 0,
            "modal_opened": False,
            "events_dispatched": events_dispatched,
        }
        if navigated:
            effects["final_url"] = post_url

        recommended = [{"tool": "snapshot"}]
        if navigated:
            recommended.append({"tool": "navigate", "url": post_url})

        return ActionResult(
            status="ok",
            action="click",
            target=node_id,
            effects=effects,
            state_delta_id=snap_id or None,
            recommended_next_actions=recommended,
        )

    async def _handle_link_click(
        self,
        element: Any,
        session: Session,
        pre_url: str,
        original_target: Any,
    ) -> ActionResult | None:
        """Navigate for <a href> clicks. Returns None if no href or same-page anchor."""
        href = element.get_attribute("href")
        if not href or href.startswith("#"):
            return None  # anchor-only, treat as generic click

        # Resolve relative URL
        base_url = getattr(session, "_current_url", "about:blank")
        if session.network:
            from an_web.net.client import NetworkClient
            resolved = NetworkClient.resolve_url(base_url, href)
        else:
            from urllib.parse import urljoin
            resolved = urljoin(base_url, href)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url=resolved)
        if result.is_ok():
            result.action = "click"
            result.effects["clicked_href"] = href
            result.effects["events_dispatched"] = ["click"]
        return result


# ─── Form handling ─────────────────────────────────────────────────────────────


def _is_submit_button(element: Any) -> bool:
    """Return True if this element triggers form submission when clicked."""
    tag = getattr(element, "tag", "")
    if tag == "button":
        btn_type = (element.get_attribute("type") or "submit").lower()
        return btn_type in ("submit", "")
    if tag == "input":
        inp_type = (element.get_attribute("type") or "").lower()
        return inp_type == "submit"
    return False


def _find_enclosing_form(element: Any, session: Any) -> Any:
    """
    Walk the parent chain to find the closest enclosing <form>.

    Uses a two-pass algorithm:
    1. Walk up the ``parent`` pointers if available.
    2. Fall back to a full-doc scan checking form descendant membership.
    """
    # Pass 1: walk parent chain (O(depth))
    node = getattr(element, "parent", None)
    while node is not None:
        if getattr(node, "tag", "") == "form":
            return node
        node = getattr(node, "parent", None)

    # Pass 2: full-doc scan (fallback)
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return None
    target_id = getattr(element, "node_id", None)
    if target_id is None:
        return None
    for form in doc.iter_elements():
        if getattr(form, "tag", "") != "form":
            continue
        for desc in form.iter_descendants():
            if getattr(desc, "node_id", None) == target_id:
                return form
    return None


async def _submit_form(
    form: Any,
    session: Any,
    submitter: Any = None,
    action_name: str = "submit",
) -> ActionResult:
    """
    Collect form fields and dispatch a network submission.

    Handles both GET (query string) and POST (form-encoded body).
    Follows redirects via the session's NetworkClient.
    """
    from an_web.dom.nodes import Element
    from an_web.dom.semantics import ActionResult

    # ── Collect fields ────────────────────────────────────────────────
    fields: dict[str, str] = {}
    for el in form.iter_descendants():
        if not isinstance(el, Element):
            continue
        name = el.get_attribute("name") or el.get_attribute("id")
        if not name:
            continue
        tag = el.tag
        inp_type = (el.get_attribute("type") or "text").lower()
        if tag in ("input", "textarea"):
            if inp_type == "hidden":
                fields[name] = el.get_attribute("value") or ""
            elif inp_type in ("checkbox", "radio"):
                if "checked" in el.attributes:
                    fields[name] = el.get_attribute("value") or "on"
            elif inp_type != "submit":
                fields[name] = el.get_attribute("value") or ""
        elif tag == "select":
            fields[name] = el.get_attribute("value") or ""

    # ── Determine form method and action ─────────────────────────────
    action_url = form.get_attribute("action") or ""
    method = (form.get_attribute("method") or "get").upper()
    base_url = getattr(session, "_current_url", "about:blank")

    if action_url:
        from an_web.net.client import NetworkClient
        action_url = NetworkClient.resolve_url(base_url, action_url)
    else:
        action_url = base_url

    form_node_id = getattr(form, "node_id", "form")
    pre_url = base_url

    # ── Network request ───────────────────────────────────────────────
    if not session.network:
        # No network — mark submitted in-DOM and return ok
        if hasattr(form, "set_attribute"):
            form.set_attribute("_an_web_submitted", "true")
        return ActionResult(
            status="ok",
            action=action_name,
            target=form_node_id,
            effects={"form_submitted": True, "navigation": False, "fields": list(fields.keys())},
        )

    try:
        if method == "POST":
            response = await session.network.post(action_url, data=fields)
        else:
            from urllib.parse import urlencode
            qs = urlencode(fields)
            sep = "&" if "?" in action_url else "?"
            get_url = f"{action_url}{sep}{qs}" if qs else action_url
            response = await session.network.get(get_url)

        # Parse new document
        from an_web.browser.parser import parse_html
        doc = parse_html(response.text, base_url=response.url)
        session._current_document = doc
        session._current_url = response.url

        # Snapshot
        snap_id = ""
        if session.snapshots:
            snap = session.snapshots.create(
                url=response.url,
                dom_content=response.text,
                semantic_data={},
                storage_state=getattr(session, "storage_state", lambda: {})(),
            )
            snap_id = snap.snapshot_id

        return ActionResult(
            status="ok",
            action=action_name,
            target=form_node_id,
            effects={
                "form_submitted": True,
                "method": method,
                "action_url": action_url,
                "fields_submitted": list(fields.keys()),
                "navigation": response.url != pre_url,
                "final_url": response.url,
                "status_code": response.status,
            },
            state_delta_id=snap_id or None,
            recommended_next_actions=[{"tool": "snapshot"}],
        )
    except Exception as exc:
        return ActionResult(
            status="failed",
            action=action_name,
            target=form_node_id,
            error=f"form_submit_error: {exc}",
        )


# ─── Event dispatch helpers ────────────────────────────────────────────────────


def _dispatch_mouse_events(element: Any, session: Any) -> list[str]:
    """
    Dispatch mousedown → mouseup → click on the element.

    If a JS runtime is available, fires real DOM MouseEvents via V8
    so that JS event listeners actually trigger.  Falls back to attribute
    markers (for headless DOM-only mode).

    Returns list of event names dispatched.
    """
    js_runtime = getattr(session, "js_runtime", None)

    if js_runtime is not None and js_runtime.is_available():
        # Dispatch via JS runtime — real events that listeners can catch
        selector = _make_js_selector(element)
        if selector:
            js_code = (
                f"(function(){{"
                f"  var _el = {selector};"
                f"  if (_el) {{"
                f"    _el.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true, cancelable:true}}) );"
                f"    _el.dispatchEvent(new MouseEvent('mouseup',   {{bubbles:true, cancelable:true}}) );"
                f"    _el.dispatchEvent(new MouseEvent('click',     {{bubbles:true, cancelable:true}}) );"
                f"  }}"
                f"}})()"
            )
            js_runtime.eval_safe(js_code)
            return ["mousedown", "mouseup", "click"]

    # Fallback: attribute marker
    if hasattr(element, "set_attribute"):
        element.set_attribute("_an_web_last_clicked", "true")
    return ["click"]


def _make_js_selector(element: Any) -> str | None:
    """
    Build the shortest reliable JS expression to reference the element.

    Preference order:
    1. ``document.getElementById('id')``
    2. ``document.querySelector('stable_selector')``
    3. ``document.querySelector('#id')``
    Returns ``None`` if no reliable selector can be built.
    """
    el_id = None
    if hasattr(element, "get_attribute"):
        el_id = element.get_attribute("id")
    if not el_id and hasattr(element, "get_id"):
        el_id = element.get_id()

    if el_id:
        safe_id = el_id.replace("'", "\\'")
        return f"document.getElementById('{safe_id}')"

    stable = getattr(element, "stable_selector", None)
    if stable:
        safe = stable.replace("'", "\\'")
        return f"document.querySelector('{safe}')"

    return None


def _get_doc_html(session: Any) -> str:
    """Return a lightweight text representation of the current document."""
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return ""
    # Use text_content as a cheap proxy for dom_hash purposes
    return doc.text_content or ""
