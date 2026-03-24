"""
Python Tool Interface — dispatch AI tool calls to AN-Web actions.

Two ways to use:

1. Low-level function::

    result = await dispatch_tool({"tool": "click", "target": "#btn"}, session)

2. High-level class (recommended)::

    interface = ANWebToolInterface(session)
    result = await interface.run({"tool": "navigate", "url": "https://x.com"})

Both accept the same two input formats:

* Flat:    ``{"tool": "click", "target": "#btn"}``
* Nested:  ``{"name": "click", "input": {"target": "#btn"}}``  (Anthropic tool_use)
"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session

log = logging.getLogger(__name__)


# ── Semantic target normalisation ─────────────────────────────────────────────

def _normalize_target(target: Any) -> str | dict[str, Any]:
    """
    Convert a ``SemanticTarget`` Pydantic object to a plain dict, or pass
    through a plain string/dict unchanged.

    Actions accept ``str | dict`` — they don't accept Pydantic models.
    """
    # Pydantic BaseModel (SemanticTarget) → plain dict
    if hasattr(target, "model_dump"):
        return {k: v for k, v in target.model_dump().items() if v is not None}
    return target


def _normalize_params(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Normalize incoming params: convert SemanticTarget objects to dicts."""
    normalized = dict(params)
    for key in ("target",):
        if key in normalized:
            normalized[key] = _normalize_target(normalized[key])
    return normalized


# ── Input parsing ─────────────────────────────────────────────────────────────

def _parse_tool_call(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Parse both flat and nested tool call formats.

    Flat (session.act style)::
        {"tool": "click", "target": "#btn"}

    Nested (Anthropic tool_use style)::
        {"name": "click", "input": {"target": "#btn"}, "type": "tool_use", ...}
    """
    if "name" in raw and "input" in raw:
        tool_name = str(raw["name"])
        params = dict(raw["input"])
    else:
        tool_name = str(raw.get("tool", ""))
        params = {k: v for k, v in raw.items() if k not in ("tool",)}
    return tool_name, params


# ── Pydantic request validation ───────────────────────────────────────────────

def _validate_request(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and normalise params via Pydantic request models.

    Returns cleaned params dict (SemanticTarget objects already normalized).
    Raises ValueError with a descriptive message on schema mismatch.
    """
    from an_web.api.models import (
        NavigateRequest, ClickRequest, TypeRequest, ClearRequest,
        SelectRequest, SubmitRequest, ExtractRequest, SnapshotRequest,
        WaitForRequest, ScrollRequest, EvalJSRequest,
    )

    _MODEL_MAP: dict[str, type] = {
        "navigate":  NavigateRequest,
        "click":     ClickRequest,
        "type":      TypeRequest,
        "clear":     ClearRequest,
        "select":    SelectRequest,
        "submit":    SubmitRequest,
        "extract":   ExtractRequest,
        "snapshot":  SnapshotRequest,
        "wait_for":  WaitForRequest,
        "scroll":    ScrollRequest,
        "eval_js":   EvalJSRequest,
    }

    model_cls = _MODEL_MAP.get(tool_name)
    if model_cls is None:
        # Unknown tool — pass params through as-is
        return params

    try:
        obj = model_cls(tool=tool_name, **params)
    except Exception as exc:
        raise ValueError(f"Invalid params for tool '{tool_name}': {exc}") from exc

    # Dump and strip the 'tool' discriminator field
    raw = obj.model_dump()
    raw.pop("tool", None)
    return raw


# ── Core dispatcher ───────────────────────────────────────────────────────────

async def _dispatch(
    tool_name: str,
    params: dict[str, Any],
    session: "Session",
) -> Any:
    """Route validated params to the appropriate action class."""

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
        "error": f"unknown_tool: {tool_name!r}",
        "recommended_next_actions": [],
    }


# ── Public dispatch function ──────────────────────────────────────────────────

async def dispatch_tool(
    tool_call: dict[str, Any],
    session: "Session",
    *,
    validate: bool = True,
    collect_artifacts: bool = True,
) -> dict[str, Any]:
    """
    Dispatch an AI tool call to the appropriate action.

    Pipeline:
        1. Parse tool name + params from flat or nested format.
        2. Validate params via Pydantic models (if validate=True).
        3. Normalize SemanticTarget objects → plain dicts.
        4. Policy pre-check via PolicyChecker.for_session(session).
        5. Dispatch to action.
        6. Convert ActionResult → dict.
        7. Collect artifacts (if collect_artifacts=True and session has collector).

    Args:
        tool_call:         Raw tool call dict.
        session:           Owning session.
        validate:          Run Pydantic validation. Default True.
        collect_artifacts: Record an action_trace artifact. Default True.

    Returns:
        ActionResult as a plain dict with at least ``"status"`` and ``"action"``.
    """
    t0 = time.perf_counter()

    # ── 1. Parse ──────────────────────────────────────────────────────────────
    try:
        tool_name, raw_params = _parse_tool_call(tool_call)
    except Exception as exc:
        return {"status": "failed", "action": "", "error": f"parse_error: {exc}"}

    if not tool_name:
        return {"status": "failed", "action": "", "error": "missing_tool_name"}

    # ── 2. Validate ───────────────────────────────────────────────────────────
    if validate:
        try:
            raw_params = _validate_request(tool_name, raw_params)
        except ValueError as exc:
            return {
                "status": "failed",
                "action": tool_name,
                "error": str(exc),
            }

    # ── 3. Normalize targets ──────────────────────────────────────────────────
    params = _normalize_params(tool_name, raw_params)

    # ── 4. Policy check ───────────────────────────────────────────────────────
    url = params.get("url") if tool_name == "navigate" else None
    policy_result = _policy_check(session, tool_name, url=url)
    if policy_result is not None:
        return policy_result

    # ── 5. Dispatch ───────────────────────────────────────────────────────────
    log.debug("dispatch_tool: %s %s", tool_name, params)
    try:
        result = await _dispatch(tool_name, params, session)
    except Exception as exc:
        log.exception("dispatch_tool: unhandled exception in %s", tool_name)
        duration_ms = (time.perf_counter() - t0) * 1000
        result_dict = {
            "status": "failed",
            "action": tool_name,
            "error": f"internal_error: {exc}",
        }
        _maybe_collect_artifact(session, tool_name, result_dict, duration_ms, collect_artifacts)
        return result_dict

    # ── 6. Normalise result ───────────────────────────────────────────────────
    if hasattr(result, "to_dict"):
        result_dict = result.to_dict()
    elif isinstance(result, dict):
        result_dict = result
    else:
        result_dict = {"status": "ok", "action": tool_name, "raw": str(result)}

    # Ensure required fields
    result_dict.setdefault("action", tool_name)
    result_dict.setdefault("status", "ok")

    # ── 7. Artifact collection ────────────────────────────────────────────────
    duration_ms = (time.perf_counter() - t0) * 1000
    _maybe_collect_artifact(session, tool_name, result_dict, duration_ms, collect_artifacts)

    return result_dict


# ── Policy helper ─────────────────────────────────────────────────────────────

def _policy_check(
    session: "Session",
    tool_name: str,
    url: str | None,
) -> dict[str, Any] | None:
    """
    Run full policy check via PolicyChecker.for_session().

    Returns:
        None              — check passed; proceed with action.
        dict              — blocked; return this as the tool result.
    """
    try:
        from an_web.policy.checker import PolicyChecker
        checker = PolicyChecker.for_session(session)
        check_result = checker.check_action(
            tool_name,
            url=url,
            consume_resources=True,
        )
        if check_result.blocked:
            return {
                "status": "blocked",
                "action": tool_name,
                "error": check_result.reason or str(check_result.violation_type),
                "recommended_next_actions": [
                    {"note": f"Policy blocked '{tool_name}': {check_result.reason}"}
                ],
            }
    except Exception:
        # Policy layer unavailable — fail open (allow)
        pass
    return None


# ── Artifact helper ───────────────────────────────────────────────────────────

def _maybe_collect_artifact(
    session: "Session",
    tool_name: str,
    result_dict: dict[str, Any],
    duration_ms: float,
    enabled: bool,
) -> None:
    """Record an ACTION_TRACE artifact on the session's collector, if present."""
    if not enabled:
        return
    collector = getattr(session, "artifacts", None)
    if collector is None:
        return
    try:
        collector.record_action_trace(
            action=tool_name,
            status=result_dict.get("status", "ok"),
            error=result_dict.get("error"),
            duration_ms=duration_ms,
            url=getattr(session, "current_url", None),
        )
    except Exception:
        pass  # never let artifact collection crash a tool call


# ── High-level interface class ────────────────────────────────────────────────

class ANWebToolInterface:
    """
    High-level AI tool interface for a single AN-Web Session.

    Wraps ``dispatch_tool`` with a fluent API, optional structured logging,
    and replay trace recording.

    Usage::

        async with ANWebEngine() as engine:
            session = await engine.create_session()
            interface = ANWebToolInterface(session)
            result = await interface.run({"tool": "navigate", "url": "https://x.com"})
            snapshot = await interface.snapshot()

    Attributes:
        session:      The backing Session.
        tool_history: List of (tool_name, result_dict) tuples in call order.
    """

    def __init__(self, session: "Session") -> None:
        self.session = session
        self.tool_history: list[tuple[str, dict[str, Any]]] = []
        self._logger = _get_struct_logger(session)

    # ── Primary interface ─────────────────────────────────────────────────────

    async def run(
        self,
        tool_call: dict[str, Any],
        *,
        validate: bool = True,
        collect_artifacts: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a single tool call and record it in ``tool_history``.

        Returns the ActionResult dict.
        """
        _, raw_params = _parse_tool_call(tool_call)
        tool_name = str(tool_call.get("name") or tool_call.get("tool", ""))

        if self._logger:
            with self._logger.action_context(f"run-{tool_name}"):
                self._logger.info(f"→ {tool_name}", data={"params": raw_params})
                result = await dispatch_tool(
                    tool_call, self.session,
                    validate=validate,
                    collect_artifacts=collect_artifacts,
                )
                self._logger.info(f"← {tool_name}", data={"status": result.get("status")})
        else:
            result = await dispatch_tool(
                tool_call, self.session,
                validate=validate,
                collect_artifacts=collect_artifacts,
            )

        self.tool_history.append((tool_name, result))
        return result

    # ── Convenience wrappers ──────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        return await self.run({"tool": "navigate", "url": url})

    async def click(self, target: str | dict[str, Any]) -> dict[str, Any]:
        return await self.run({"tool": "click", "target": target})

    async def type(self, target: str | dict[str, Any], text: str) -> dict[str, Any]:
        return await self.run({"tool": "type", "target": target, "text": text})

    async def snapshot(self) -> dict[str, Any]:
        return await self.run({"tool": "snapshot"})

    async def extract(self, query: str) -> dict[str, Any]:
        return await self.run({"tool": "extract", "query": query})

    async def eval_js(self, script: str) -> dict[str, Any]:
        return await self.run({"tool": "eval_js", "script": script})

    async def wait_for(
        self,
        condition: str = "network_idle",
        selector: str | None = None,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"tool": "wait_for", "condition": condition, "timeout_ms": timeout_ms}
        if selector:
            params["selector"] = selector
        return await self.run(params)

    # ── Replay helpers ────────────────────────────────────────────────────────

    def history_as_trace(self) -> dict[str, Any]:
        """Export tool_history as a ReplayTrace-compatible dict."""
        from an_web.tracing.replay import ReplayTrace
        trace = ReplayTrace.new(
            session_id=self.session.session_id,
            source="ANWebToolInterface",
        )
        for tool_name, result in self.tool_history:
            trace.add_step(
                tool_name,
                expected_status=result.get("status"),
            )
        return trace.to_dict()

    # ── Dunder ────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ANWebToolInterface("
            f"session={self.session.session_id[:8]}..., "
            f"calls={len(self.tool_history)}"
            f")"
        )


# ── Logger helper ─────────────────────────────────────────────────────────────

def _get_struct_logger(session: "Session") -> Any:
    """Return StructuredLogger if available on session, else None."""
    # sessions don't currently carry a StructuredLogger — return None
    # (callers can attach one via session.struct_logger = get_logger(...))
    return getattr(session, "struct_logger", None)
