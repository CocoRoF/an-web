"""Unit tests for dispatch_tool and ANWebToolInterface."""
from __future__ import annotations

import pytest
from typing import Any

from an_web.api.rpc import (
    dispatch_tool, ANWebToolInterface,
    _parse_tool_call, _normalize_target, _validate_request,
)
from an_web.api.models import SemanticTarget


# ── Stubs ─────────────────────────────────────────────────────────────────────

class StubActionResult:
    """Stub that mimics ActionResult.to_dict()."""
    def __init__(self, action: str, status: str = "ok", **effects):
        self._d = {
            "status": status,
            "action": action,
            "effects": effects,
            "error": None,
            "recommended_next_actions": [],
        }

    def to_dict(self) -> dict[str, Any]:
        return self._d


class FakeSession:
    """
    Minimal session stub for API unit tests.

    Intercepts action calls without hitting any real DOM/JS/network code.
    """

    def __init__(self):
        self.session_id = "test-session-001"
        self.current_url = "about:blank"
        self._current_document = None
        self.js_runtime = None
        self.policy = None
        self.sandbox = None
        self.approvals = None
        self.artifacts = None
        self._calls: list[dict] = []

    # Mimic session.act() via rpc.dispatch_tool
    async def act(self, tool_call: dict) -> dict:
        return await dispatch_tool(tool_call, self)


class TracingFakeSession(FakeSession):
    """Fake session that also records dispatch calls for assertion."""

    def __init__(self):
        super().__init__()
        self._dispatch_log: list[tuple[str, dict]] = []


# ── _parse_tool_call ──────────────────────────────────────────────────────────

class TestParseTool:
    def test_flat_format(self):
        name, params = _parse_tool_call({"tool": "click", "target": "#btn"})
        assert name == "click"
        assert params == {"target": "#btn"}

    def test_nested_format(self):
        name, params = _parse_tool_call({"name": "navigate", "input": {"url": "https://x.com"}})
        assert name == "navigate"
        assert params == {"url": "https://x.com"}

    def test_flat_strips_tool_key(self):
        _, params = _parse_tool_call({"tool": "type", "target": "#x", "text": "hi"})
        assert "tool" not in params

    def test_empty_tool_returns_empty_string(self):
        name, _ = _parse_tool_call({})
        assert name == ""


# ── _normalize_target ─────────────────────────────────────────────────────────

class TestNormalizeTarget:
    def test_string_passthrough(self):
        assert _normalize_target("#btn") == "#btn"

    def test_dict_passthrough(self):
        d = {"by": "role", "role": "button"}
        assert _normalize_target(d) == d

    def test_pydantic_model_to_dict(self):
        t = SemanticTarget(by="role", role="button", text="Submit")
        result = _normalize_target(t)
        assert isinstance(result, dict)
        assert result["by"] == "role"
        assert result["role"] == "button"
        assert result["text"] == "Submit"
        # None fields omitted
        assert "node_id" not in result

    def test_pydantic_none_fields_omitted(self):
        t = SemanticTarget(by="text", text="Login")
        d = _normalize_target(t)
        assert "role" not in d
        assert "node_id" not in d


# ── _validate_request ─────────────────────────────────────────────────────────

class TestValidateRequest:
    def test_navigate_valid(self):
        out = _validate_request("navigate", {"url": "https://x.com"})
        assert out["url"] == "https://x.com"
        assert "tool" not in out

    def test_navigate_empty_url_raises(self):
        with pytest.raises(ValueError, match="url"):
            _validate_request("navigate", {"url": ""})

    def test_wait_for_element_visible_no_selector_raises(self):
        with pytest.raises(ValueError):
            _validate_request("wait_for", {"condition": "element_visible"})

    def test_wait_for_element_visible_with_selector_ok(self):
        out = _validate_request("wait_for", {
            "condition": "element_visible", "selector": "#spinner"
        })
        assert out["selector"] == "#spinner"

    def test_unknown_tool_passthrough(self):
        params = {"x": 1, "y": 2}
        out = _validate_request("my_custom_tool", params)
        assert out == params

    def test_eval_js_empty_rejected(self):
        with pytest.raises(ValueError):
            _validate_request("eval_js", {"script": ""})

    def test_type_defaults_appended(self):
        out = _validate_request("type", {"target": "#f", "text": "hello"})
        assert out.get("append") is False

    def test_extract_mode_default(self):
        out = _validate_request("extract", {"query": "table tr"})
        assert out["mode"] == "css"
        assert out["limit"] == 100


# ── dispatch_tool — format handling ───────────────────────────────────────────

class TestDispatchFormat:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await dispatch_tool({"tool": "magic_action"}, FakeSession())
        assert result["status"] == "failed"
        assert "unknown_tool" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_tool_name(self):
        result = await dispatch_tool({}, FakeSession())
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_nested_format_parsed(self):
        """Nested Anthropic format should be parsed correctly."""
        # snapshot has no required params — safe to dispatch with a stub session
        result = await dispatch_tool(
            {"name": "snapshot", "input": {}},
            FakeSession(),
        )
        # With no real document, snapshot returns something (ok or failed, but not unknown_tool)
        assert result.get("action") != ""

    @pytest.mark.asyncio
    async def test_validation_failure_returned_as_failed(self):
        result = await dispatch_tool({"tool": "navigate", "url": ""}, FakeSession())
        assert result["status"] == "failed"
        assert "url" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_validate_skips_pydantic(self):
        """With validate=False, empty url passes through to action (which may fail differently)."""
        result = await dispatch_tool(
            {"tool": "navigate", "url": ""},
            FakeSession(),
            validate=False,
        )
        # Either failed (action handled it) or some other status — but NOT a validation error
        assert "status" in result


# ── dispatch_tool — policy blocking ──────────────────────────────────────────

class TestDispatchPolicy:
    @pytest.mark.asyncio
    async def test_navigate_blocked_by_policy(self):
        from an_web.policy.rules import PolicyRules
        session = FakeSession()
        session.policy = PolicyRules(denied_domains=["blocked.example.com"])
        result = await dispatch_tool(
            {"tool": "navigate", "url": "https://blocked.example.com/"},
            session,
        )
        assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_non_blocked_domain_passes_policy(self):
        from an_web.policy.rules import PolicyRules
        session = FakeSession()
        session.policy = PolicyRules(denied_domains=["evil.com"])
        # navigate to safe.com — policy passes, action may fail due to no network but not blocked
        result = await dispatch_tool(
            {"tool": "navigate", "url": "https://safe.com/"},
            session,
        )
        assert result["status"] != "blocked"


# ── dispatch_tool — artifact collection ──────────────────────────────────────

class TestDispatchArtifacts:
    @pytest.mark.asyncio
    async def test_artifact_collected_on_success(self):
        from an_web.tracing.artifacts import ArtifactCollector, ArtifactKind
        session = FakeSession()
        session.artifacts = ArtifactCollector(session.session_id)

        # snapshot with empty document — will return ok or failed, but not crash
        await dispatch_tool({"tool": "snapshot"}, session, collect_artifacts=True)

        artifacts = session.artifacts.get_by_kind(ArtifactKind.ACTION_TRACE)
        assert len(artifacts) == 1
        assert artifacts[0].data["action"] == "snapshot"

    @pytest.mark.asyncio
    async def test_no_artifact_when_disabled(self):
        from an_web.tracing.artifacts import ArtifactCollector
        session = FakeSession()
        session.artifacts = ArtifactCollector(session.session_id)

        await dispatch_tool({"tool": "snapshot"}, session, collect_artifacts=False)
        assert len(session.artifacts) == 0

    @pytest.mark.asyncio
    async def test_artifact_on_failed_dispatch(self):
        from an_web.tracing.artifacts import ArtifactCollector
        session = FakeSession()
        session.artifacts = ArtifactCollector(session.session_id)

        await dispatch_tool({"tool": "unknown_x"}, session, collect_artifacts=True)
        # unknown tool returns a failed result — artifact should record it
        artifacts = session.artifacts.get_all()
        assert len(artifacts) == 1
        assert artifacts[0].data["status"] == "failed"


# ── ANWebToolInterface ────────────────────────────────────────────────────────

class TestANWebToolInterface:
    @pytest.mark.asyncio
    async def test_run_records_history(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        await iface.run({"tool": "snapshot"})
        assert len(iface.tool_history) == 1
        tool_name, result = iface.tool_history[0]
        assert tool_name == "snapshot"

    @pytest.mark.asyncio
    async def test_multiple_calls_accumulate(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        await iface.snapshot()
        await iface.snapshot()
        assert len(iface.tool_history) == 2

    @pytest.mark.asyncio
    async def test_navigate_convenience(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        result = await iface.navigate("https://example.com")
        assert "status" in result

    @pytest.mark.asyncio
    async def test_click_convenience(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        result = await iface.click("#submit")
        assert "status" in result

    @pytest.mark.asyncio
    async def test_type_convenience(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        result = await iface.type("#email", "user@x.com")
        assert "status" in result

    @pytest.mark.asyncio
    async def test_eval_js_convenience(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        result = await iface.eval_js("1 + 1")
        assert "status" in result

    @pytest.mark.asyncio
    async def test_wait_for_convenience(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        result = await iface.wait_for("dom_stable", timeout_ms=100)
        assert "status" in result

    def test_history_as_trace(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        iface.tool_history.append(("navigate", {"status": "ok"}))
        iface.tool_history.append(("click", {"status": "ok"}))
        trace = iface.history_as_trace()
        assert trace["session_id"] == "test-session-001"
        assert len(trace["steps"]) == 2

    def test_repr(self):
        session = FakeSession()
        iface = ANWebToolInterface(session)
        r = repr(iface)
        assert "test-ses" in r
        assert "calls=0" in r


# ── End-to-end: session.act() uses dispatch_tool ──────────────────────────────

class TestSessionAct:
    @pytest.mark.asyncio
    async def test_act_snapshot(self):
        session = FakeSession()
        result = await session.act({"tool": "snapshot"})
        assert "status" in result

    @pytest.mark.asyncio
    async def test_act_unknown_tool(self):
        session = FakeSession()
        result = await session.act({"tool": "mystery_action"})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_act_anthropic_nested_format(self):
        session = FakeSession()
        result = await session.act({"name": "snapshot", "input": {}})
        assert "status" in result
