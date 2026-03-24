"""Tool dispatch router — maps tool calls to action executors."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session


async def dispatch_tool(
    tool_call: dict[str, Any],
    session: Session,
) -> dict[str, Any]:
    """
    Dispatch an AI tool call to the appropriate action.

    Accepts both flat dict format::

        {"tool": "click", "target": "#btn"}

    And nested input_schema format::

        {"name": "click", "input": {"target": "#btn"}}
    """
    # Normalize format
    if "name" in tool_call and "input" in tool_call:
        tool_name = tool_call["name"]
        params = tool_call["input"]
    else:
        tool_name = tool_call.get("tool", "")
        params = {k: v for k, v in tool_call.items() if k != "tool"}

    # Policy pre-check
    if session.policy and session.policy.requires_approval(tool_name):
        return {
            "status": "blocked",
            "action": tool_name,
            "error": "requires_approval",
            "recommended_next_actions": [
                {"note": f"Action '{tool_name}' requires explicit approval"}
            ],
        }

    if session.policy and not session.policy.check_rate_limit():
        return {
            "status": "blocked",
            "action": tool_name,
            "error": "rate_limit_exceeded",
        }

    # Dispatch
    result = await _dispatch(tool_name, params, session)

    # Convert ActionResult to dict if needed
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return result


async def _dispatch(
    tool_name: str,
    params: dict[str, Any],
    session: Session,
) -> Any:
    if tool_name == "navigate":
        from an_web.actions.navigate import NavigateAction
        return await NavigateAction().execute(session, **params)

    if tool_name == "click":
        from an_web.actions.click import ClickAction
        return await ClickAction().execute(session, **params)

    if tool_name == "type":
        from an_web.actions.input import TypeAction
        return await TypeAction().execute(session, **params)

    if tool_name == "clear":
        from an_web.actions.input import ClearAction
        return await ClearAction().execute(session, **params)

    if tool_name == "select":
        from an_web.actions.input import SelectAction
        return await SelectAction().execute(session, **params)

    if tool_name == "submit":
        from an_web.actions.submit import SubmitAction
        return await SubmitAction().execute(session, **params)

    if tool_name == "extract":
        from an_web.actions.extract import ExtractAction
        return await ExtractAction().execute(session, **params)

    if tool_name == "snapshot":
        from an_web.semantic.extractor import SemanticExtractor
        semantics = await SemanticExtractor().extract(session=session)
        return semantics.to_dict()

    if tool_name == "scroll":
        from an_web.actions.scroll import ScrollAction
        return await ScrollAction().execute(session, **params)

    if tool_name == "wait_for":
        from an_web.actions.wait_for import WaitForAction
        return await WaitForAction().execute(session, **params)

    if tool_name == "eval_js":
        from an_web.actions.eval_js import EvalJSAction
        return await EvalJSAction().execute(session, **params)

    return {
        "status": "failed",
        "action": tool_name,
        "error": f"unknown_tool: {tool_name}",
    }
