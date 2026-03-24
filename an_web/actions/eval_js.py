"""eval_js action — execute JavaScript in page context."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class EvalJSAction(Action):
    async def execute(
        self, session: Session,
        script: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        js_runtime = getattr(session, "_js_runtime", None)
        if js_runtime is None:
            return self._make_failure("eval_js", "js_runtime_not_initialized")

        try:
            result = js_runtime.eval(script)
            return ActionResult(
                status="ok",
                action="eval_js",
                effects={"result": str(result)},
            )
        except Exception as e:
            return self._make_failure("eval_js", f"js_error: {e}")
