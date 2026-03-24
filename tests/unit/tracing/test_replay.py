"""Unit tests for ReplayStep, ReplayTrace, and ReplayEngine."""
from __future__ import annotations

import json
import pytest
from an_web.tracing.replay import (
    ReplayStep, ReplayTrace, ReplayResult, StepResult, ReplayEngine,
)


# ── ReplayStep ────────────────────────────────────────────────────────────────

class TestReplayStep:
    def test_from_dict_basic(self):
        s = ReplayStep.from_dict({"action": "click", "selector": "#btn"})
        assert s.action == "click"
        assert s.params["selector"] == "#btn"
        assert s.step_id.startswith("step-")

    def test_from_dict_tool_alias(self):
        s = ReplayStep.from_dict({"tool": "navigate", "url": "https://x.com"})
        assert s.action == "navigate"
        assert s.params["url"] == "https://x.com"

    def test_from_dict_assertions(self):
        s = ReplayStep.from_dict({
            "action": "navigate",
            "url": "https://x.com",
            "expected_status": "ok",
            "expected_url": "https://x.com/home",
        })
        assert s.expected_status == "ok"
        assert s.expected_url == "https://x.com/home"

    def test_to_dict_roundtrip(self):
        s = ReplayStep(
            step_id="step-abc",
            action="click",
            params={"selector": "#btn"},
            expected_status="ok",
        )
        d = s.to_dict()
        assert d["step_id"] == "step-abc"
        assert d["action"] == "click"
        assert d["params"]["selector"] == "#btn"
        assert d["expected_status"] == "ok"

    def test_to_dict_omits_optional(self):
        s = ReplayStep(step_id="s1", action="click")
        d = s.to_dict()
        assert "expected_status" not in d
        assert "expected_url" not in d
        assert "wait_ms" not in d

    def test_wait_ms_included_when_set(self):
        s = ReplayStep(step_id="s1", action="click", wait_ms=500.0)
        d = s.to_dict()
        assert d["wait_ms"] == 500.0


# ── ReplayTrace ───────────────────────────────────────────────────────────────

class TestReplayTrace:
    def test_new_factory(self):
        t = ReplayTrace.new("my-session", source="test")
        assert t.session_id == "my-session"
        assert t.metadata["source"] == "test"
        assert t.trace_id.startswith("trace-")
        assert len(t.steps) == 0

    def test_add_step(self):
        t = ReplayTrace.new("s1")
        step = t.add_step("click", {"selector": "#btn"})
        assert step.action == "click"
        assert len(t.steps) == 1

    def test_add_step_with_assertions(self):
        t = ReplayTrace.new("s1")
        step = t.add_step(
            "navigate",
            {"url": "https://x.com"},
            expected_status="ok",
            expected_url="https://x.com/home",
        )
        assert step.expected_status == "ok"
        assert step.expected_url == "https://x.com/home"

    def test_to_dict_roundtrip(self):
        t = ReplayTrace.new("s1")
        t.add_step("click", {"selector": "#a"})
        t.add_step("navigate", {"url": "https://x.com"})
        d = t.to_dict()
        assert d["session_id"] == "s1"
        assert len(d["steps"]) == 2

    def test_to_json_valid(self):
        t = ReplayTrace.new("s1")
        t.add_step("click")
        j = t.to_json()
        obj = json.loads(j)
        assert len(obj["steps"]) == 1

    def test_from_dict_roundtrip(self):
        t = ReplayTrace.new("s1")
        t.add_step("click", {"selector": "#a"})
        t.add_step("navigate", {"url": "https://x.com"}, expected_status="ok")
        t2 = ReplayTrace.from_dict(t.to_dict())
        assert t2.trace_id == t.trace_id
        assert t2.session_id == "s1"
        assert len(t2.steps) == 2
        assert t2.steps[1].expected_status == "ok"

    def test_from_json_roundtrip(self):
        t = ReplayTrace.new("s1")
        t.add_step("click")
        t2 = ReplayTrace.from_json(t.to_json())
        assert t2.trace_id == t.trace_id


# ── ReplayResult ──────────────────────────────────────────────────────────────

class TestReplayResult:
    def test_succeeded_all_ok(self):
        rr = ReplayResult(trace_id="t1", session_id="s1")
        rr.steps.append(StepResult("s1", "click", "ok", {}))
        rr.steps.append(StepResult("s2", "navigate", "ok", {}))
        assert rr.succeeded is True

    def test_succeeded_false_on_failure(self):
        rr = ReplayResult(trace_id="t1", session_id="s1")
        rr.steps.append(StepResult("s1", "click", "ok", {}))
        rr.steps.append(StepResult("s2", "navigate", "assertion_failed", {}))
        assert rr.succeeded is False

    def test_failed_steps(self):
        rr = ReplayResult(trace_id="t1", session_id="s1")
        rr.steps.append(StepResult("s1", "click", "ok", {}))
        rr.steps.append(StepResult("s2", "navigate", "error", {}))
        assert len(rr.failed_steps) == 1

    def test_to_dict_keys(self):
        rr = ReplayResult(trace_id="t1", session_id="s1")
        rr.steps.append(StepResult("s1", "click", "ok", {}, duration_ms=20.0))
        d = rr.to_dict()
        assert d["trace_id"] == "t1"
        assert "succeeded" in d
        assert len(d["steps"]) == 1
        assert d["steps"][0]["duration_ms"] == 20.0


# ── ReplayEngine with fake session ────────────────────────────────────────────

class FakeSession:
    """Minimal session stub for ReplayEngine tests."""

    def __init__(self):
        self.session_id = "fake-001"
        self.current_url = "https://start.com"
        self._calls: list[dict] = []

    async def act(self, params: dict) -> dict:
        self._calls.append(params)
        tool = params.get("tool", "")
        if tool == "navigate":
            self.current_url = params.get("url", self.current_url)
            return {"status": "ok", "url": self.current_url}
        if tool == "click":
            return {"status": "ok"}
        if tool == "fail_action":
            return {"status": "failed", "error": "intentional"}
        return {"status": "ok"}


class TestReplayEngine:
    @pytest.fixture
    def engine(self):
        return ReplayEngine()

    @pytest.fixture
    def session(self):
        return FakeSession()

    @pytest.mark.asyncio
    async def test_replay_raw_list(self, engine, session):
        log = [
            {"action": "navigate", "url": "https://x.com"},
            {"action": "click", "selector": "#btn"},
        ]
        results = await engine.replay(session, log)
        assert len(results) == 2
        assert results[0]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_replay_trace_basic(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("navigate", {"url": "https://x.com"})
        t.add_step("click", {"selector": "#btn"})
        result = await engine.replay_trace(session, t)
        assert result.succeeded is True
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_replay_trace_assertion_status_pass(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("click", expected_status="ok")
        result = await engine.replay_trace(session, t)
        assert result.steps[0].status == "ok"
        assert result.steps[0].assertion_error is None

    @pytest.mark.asyncio
    async def test_replay_trace_assertion_status_fail(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("fail_action", expected_status="ok")
        result = await engine.replay_trace(session, t)
        assert result.steps[0].status == "assertion_failed"
        assert "expected status" in result.steps[0].assertion_error

    @pytest.mark.asyncio
    async def test_replay_trace_assertion_url_pass(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("navigate", {"url": "https://x.com"}, expected_url="https://x.com")
        result = await engine.replay_trace(session, t)
        assert result.steps[0].status == "ok"

    @pytest.mark.asyncio
    async def test_replay_trace_assertion_url_fail(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("navigate", {"url": "https://x.com"}, expected_url="https://other.com")
        result = await engine.replay_trace(session, t)
        assert result.steps[0].status == "assertion_failed"
        assert "expected url" in result.steps[0].assertion_error

    @pytest.mark.asyncio
    async def test_replay_trace_exception_marked_error(self, engine):
        """If session.act() raises, step status is 'error'."""
        class ErrorSession:
            session_id = "e1"
            current_url = ""
            async def act(self, _):
                raise RuntimeError("connection lost")

        t = ReplayTrace.new("e1")
        t.add_step("navigate", {"url": "https://x.com"})
        result = await engine.replay_trace(ErrorSession(), t)
        assert result.steps[0].status == "error"
        assert "connection lost" in result.steps[0].result.get("error", "")

    @pytest.mark.asyncio
    async def test_replay_continues_after_assertion_failure(self, engine, session):
        """All steps are executed even if one assertion fails."""
        t = ReplayTrace.new("fake-001")
        t.add_step("fail_action", expected_status="ok")
        t.add_step("click")
        result = await engine.replay_trace(session, t)
        assert len(result.steps) == 2
        assert result.steps[1].status == "ok"

    @pytest.mark.asyncio
    async def test_replay_result_duration_set(self, engine, session):
        t = ReplayTrace.new("fake-001")
        t.add_step("click")
        result = await engine.replay_trace(session, t)
        assert result.total_duration_ms >= 0.0

    @pytest.mark.asyncio
    async def test_replay_empty_trace(self, engine, session):
        t = ReplayTrace.new("fake-001")
        result = await engine.replay_trace(session, t)
        assert result.succeeded is True
        assert len(result.steps) == 0
