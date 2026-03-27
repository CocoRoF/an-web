"""eval_js action — execute JavaScript in the current page context."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class EvalJSAction(Action):
    """
    Evaluate arbitrary JavaScript in the current page's V8 context.

    The result is stringified and returned in ``effects["result"]``.
    Useful for inspecting page state, triggering custom JS logic, or
    extracting computed values that the semantic layer doesn't expose.

    ``effects`` keys:
    - ``result``:     String representation of the JS return value.
    - ``raw_type``:   Python type name of the converted return value.
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

        # session.js_runtime (not session._js_runtime)
        js_runtime = getattr(session, "js_runtime", None)
        if js_runtime is None or not js_runtime.is_available():
            return ActionResult(
                status="ok",
                action="eval_js",
                effects={"result": None, "raw_type": "NoneType", "available": False},
            )

        eval_result = js_runtime.eval_safe(script)
        # Drain microtasks — script may have queued Promise continuations
        await js_runtime.drain_microtasks()

        if not eval_result.ok:
            err = eval_result.error
            return self._make_failure(
                "eval_js",
                f"js_error: {err.message if err else 'unknown'}",
            )

        raw = eval_result.value
        return ActionResult(
            status="ok",
            action="eval_js",
            effects={
                "result": str(raw) if raw is not None else None,
                "raw_value": raw,
                "raw_type": type(raw).__name__,
                "available": True,
            },
        )
