"""
Form submit action.

Accepts either:
  - A <form> element directly as the target, or
  - Any element inside a form (walks up to find the enclosing form).

Then delegates to the shared _submit_form() helper from click.py which
handles field collection, GET/POST dispatch, redirect following, and
snapshot creation.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)


class SubmitAction(Action):
    """
    Submit a form.

    Accepted ``target`` formats:
    - CSS selector of a <form> or any element inside one.
    - ``{"by": "node_id", "node_id": "form-el-id"}``
    - ``{"by": "role", "role": "form"}``

    Effects keys (from _submit_form):
    - ``form_submitted``:     True
    - ``method``:             "GET" or "POST"
    - ``action_url``:         The URL the form was submitted to.
    - ``fields_submitted``:   List of field names included.
    - ``navigation``:         True if URL changed.
    - ``final_url``:          New URL (if navigation occurred).
    - ``status_code``:        HTTP status of the submission response.
    """

    async def execute(
        self,
        session: Session,
        target: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.actions.click import _find_enclosing_form, _submit_form

        # ── 0. Policy check ───────────────────────────────────────────
        policy_failure = self._check_policy(
            session, "submit", consume_resources=False
        )
        if policy_failure is not None:
            return policy_failure

        # ── 1. Resolve target ─────────────────────────────────────────
        element = await self._resolve_target(target, session)
        if element is None:
            return self._make_failure(
                "submit",
                "target_not_found",
                target=str(target),
                recommended=[
                    {"tool": "snapshot"},
                    {"tool": "extract", "query": "form"},
                ],
            )

        # ── 2. Find the form ──────────────────────────────────────────
        tag = getattr(element, "tag", "")
        if tag == "form":
            form = element
        else:
            form = _find_enclosing_form(element, session)

        if form is None:
            return self._make_failure(
                "submit",
                "no_form_found",
                target=getattr(element, "node_id", str(target)),
                recommended=[
                    {"tool": "snapshot", "note": "verify element is inside a form"},
                    {"tool": "extract", "query": "form"},
                ],
            )

        # ── 3. Check for JS submit event handler (dispatch + drain) ───
        js_runtime = getattr(session, "js_runtime", None)
        if js_runtime is not None and js_runtime.is_available():
            from an_web.actions.click import _make_js_selector
            selector = _make_js_selector(form)
            if selector:
                js_code = (
                    f"(function(){{"
                    f"  var _f = {selector};"
                    f"  if (_f) _f.dispatchEvent(new Event('submit', {{bubbles:true, cancelable:true}}));"
                    f"}})()"
                )
                js_runtime.eval_safe(js_code)
                if session.scheduler:
                    await session.scheduler.drain_microtasks()

        # ── 4. Network submission via shared helper ───────────────────
        result = await _submit_form(form, session, action_name="submit")
        return result
