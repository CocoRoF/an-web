"""eval_js action — execute JavaScript in the current page context."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

# Stringified results larger than this are truncated — an AI agent
# consuming tool output never needs multi-megabyte strings.
_MAX_RESULT_CHARS = 100_000

# How long to settle a Promise returned by the script (seconds).  The
# settle loop performs any fetch/XHR the script started, so
# ``eval_js("fetch(url).then(r => r.json())")`` returns the JSON.
_PROMISE_BUDGET_S = 10.0

# Wraps the user script; if the completion value is a thenable, park it
# in globals and signal the host to run the settle loop.  The direct
# eval runs inside a closure that shadows fetch/XMLHttpRequest with the
# pristine host implementations — pages like Next.js monkey-patch the
# globals in ways that break outside their own runtime.
_ASYNC_WRAPPER = """
(function() {
    globalThis.__anweb_async_state = 'none';
    globalThis.__anweb_async_value = null;
    globalThis.__anweb_async_error = null;
    var __r = (function(fetch, XMLHttpRequest) {
        return eval(%s);
    })(globalThis.__anweb_fetch || globalThis.fetch,
       globalThis.__anweb_xhr || globalThis.XMLHttpRequest);
    if (__r !== null && (typeof __r === 'object' || typeof __r === 'function')
            && typeof __r.then === 'function') {
        globalThis.__anweb_async_state = 'pending';
        __r.then(function(v) {
            globalThis.__anweb_async_state = 'done';
            globalThis.__anweb_async_value = v;
        }, function(e) {
            globalThis.__anweb_async_state = 'error';
            globalThis.__anweb_async_error = String((e && e.message) || e);
        });
        return '__anweb_promise__';
    }
    globalThis.__anweb_async_value = __r;
    return '__anweb_sync__';
})()
"""


class EvalJSAction(Action):
    """
    Evaluate arbitrary JavaScript in the current page's V8 context.

    Promise results are awaited (like Playwright's ``evaluate``): the
    settle loop performs fetch/XHR requests the script started, fires
    ready timers, and returns the resolved value.

    ``effects`` keys:
    - ``result``:     String representation of the JS return value.
    - ``raw_value``:  JSON-converted return value where possible.
    - ``raw_type``:   Python type name of the converted return value.
    - ``awaited``:    True if the script returned a Promise that was awaited.
    - ``available``:  Whether the JS runtime was available.
    """

    async def execute(
        self,
        session: Session,
        script: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        if not script:
            return self._make_failure("eval_js", "empty_script")

        js_runtime = getattr(session, "js_runtime", None)
        if js_runtime is None or not js_runtime.is_available():
            return ActionResult(
                status="ok",
                action="eval_js",
                effects={"result": None, "raw_type": "NoneType", "available": False},
            )

        wrapped = _ASYNC_WRAPPER % json.dumps(script)
        eval_result = js_runtime.eval_safe(wrapped)
        await js_runtime.drain_microtasks()

        if not eval_result.ok:
            err = eval_result.error
            return self._make_failure(
                "eval_js",
                f"js_error: {err.message if err else 'unknown'}",
            )

        awaited = False
        if eval_result.value == "__anweb_promise__":
            awaited = True
            await self._await_promise(session, js_runtime)

        state = js_runtime.eval_safe("globalThis.__anweb_async_state").value
        if state == "error":
            err_msg = js_runtime.eval_safe("globalThis.__anweb_async_error").value
            return self._make_failure("eval_js", f"js_error: {err_msg}")
        if state == "pending":
            return self._make_failure(
                "eval_js",
                f"promise_timeout: script's Promise did not settle "
                f"within {_PROMISE_BUDGET_S:.0f}s",
            )

        raw = js_runtime.eval_safe(
            "JSON.stringify(globalThis.__anweb_async_value === undefined "
            "? null : globalThis.__anweb_async_value)"
        ).value
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            value = raw

        text = None if value is None else (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        truncated = False
        if text is not None and len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS]
            truncated = True
        return ActionResult(
            status="ok",
            action="eval_js",
            effects={
                "result": text,
                "raw_value": value,
                "raw_type": type(value).__name__,
                "available": True,
                "awaited": awaited,
                "truncated": truncated,
            },
        )

    async def _await_promise(self, session: Session, js_runtime: Any) -> None:
        """Run the settle loop until the parked Promise resolves."""
        import asyncio
        import time

        deadline = time.monotonic() + _PROMISE_BUDGET_S
        while time.monotonic() < deadline:
            state = js_runtime.eval_safe("globalThis.__anweb_async_state").value
            if state in ("done", "error"):
                return
            worked = await self._settle_after_action(session, budget_s=0.5)
            if not worked:
                await asyncio.sleep(0.05)