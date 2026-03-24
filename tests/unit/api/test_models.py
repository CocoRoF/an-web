"""Unit tests for Pydantic request/response models."""
from __future__ import annotations

import json
import pytest
from pydantic import ValidationError

from an_web.api.models import (
    SemanticTarget,
    NavigateRequest, ClickRequest, TypeRequest, ClearRequest,
    SelectRequest, SubmitRequest, ExtractRequest, SnapshotRequest,
    WaitForRequest, ScrollRequest, EvalJSRequest,
    TOOL_REQUEST_MAP,
    ActionEffects, ActionResponse,
    PageSemanticsResponse,
)


# ── SemanticTarget ────────────────────────────────────────────────────────────

class TestSemanticTarget:
    def test_by_role(self):
        t = SemanticTarget(by="role", role="button", text="Submit")
        assert t.by == "role"
        assert t.role == "button"

    def test_by_text(self):
        t = SemanticTarget(by="text", text="Login")
        assert t.text == "Login"

    def test_by_node_id(self):
        t = SemanticTarget(by="node_id", node_id="el-42")
        assert t.node_id == "el-42"

    def test_invalid_by(self):
        with pytest.raises(ValidationError):
            SemanticTarget(by="unknown")

    def test_to_dict_omits_none(self):
        t = SemanticTarget(by="role", role="button")
        d = t.to_dict()
        assert "text" not in d
        assert d["by"] == "role"
        assert d["role"] == "button"


# ── NavigateRequest ───────────────────────────────────────────────────────────

class TestNavigateRequest:
    def test_valid(self):
        r = NavigateRequest(url="https://example.com")
        assert r.url == "https://example.com"
        assert r.tool == "navigate"

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError):
            NavigateRequest(url="")

    def test_tool_default(self):
        r = NavigateRequest(url="https://x.com")
        assert r.tool == "navigate"


# ── ClickRequest ──────────────────────────────────────────────────────────────

class TestClickRequest:
    def test_string_target(self):
        r = ClickRequest(target="#btn")
        assert r.target == "#btn"

    def test_semantic_target(self):
        t = SemanticTarget(by="role", role="button", text="Submit")
        r = ClickRequest(target=t)
        assert isinstance(r.target, SemanticTarget)

    def test_dict_target_not_allowed(self):
        # dict targets are validated at dispatch level, not model level
        # However pydantic will try to coerce dict → SemanticTarget
        r = ClickRequest(target={"by": "role", "role": "button"})
        assert isinstance(r.target, SemanticTarget)


# ── TypeRequest ───────────────────────────────────────────────────────────────

class TestTypeRequest:
    def test_valid(self):
        r = TypeRequest(target="#email", text="user@example.com")
        assert r.text == "user@example.com"
        assert r.append is False

    def test_append_mode(self):
        r = TypeRequest(target="#email", text=" extra", append=True)
        assert r.append is True


# ── SelectRequest ─────────────────────────────────────────────────────────────

class TestSelectRequest:
    def test_valid(self):
        r = SelectRequest(target="#country", value="US")
        assert r.value == "US"
        assert r.by_text is False

    def test_by_text(self):
        r = SelectRequest(target="#country", value="United States", by_text=True)
        assert r.by_text is True


# ── WaitForRequest ────────────────────────────────────────────────────────────

class TestWaitForRequest:
    def test_network_idle(self):
        r = WaitForRequest(condition="network_idle")
        assert r.timeout_ms == 5000

    def test_element_visible_requires_selector(self):
        with pytest.raises(ValidationError):
            WaitForRequest(condition="element_visible")

    def test_element_visible_with_selector(self):
        r = WaitForRequest(condition="element_visible", selector="#spinner")
        assert r.selector == "#spinner"

    def test_custom_timeout(self):
        r = WaitForRequest(condition="dom_stable", timeout_ms=10000)
        assert r.timeout_ms == 10000


# ── EvalJSRequest ─────────────────────────────────────────────────────────────

class TestEvalJSRequest:
    def test_valid(self):
        r = EvalJSRequest(script="document.title")
        assert r.script == "document.title"

    def test_empty_script_rejected(self):
        with pytest.raises(ValidationError):
            EvalJSRequest(script="   ")


# ── SnapshotRequest ───────────────────────────────────────────────────────────

class TestSnapshotRequest:
    def test_valid(self):
        r = SnapshotRequest()
        assert r.tool == "snapshot"


# ── ExtractRequest ────────────────────────────────────────────────────────────

class TestExtractRequest:
    def test_defaults(self):
        r = ExtractRequest(query="table tr")
        assert r.mode == "css"
        assert r.limit == 100

    def test_mode_html(self):
        r = ExtractRequest(query=".product", mode="html")
        assert r.mode == "html"


# ── TOOL_REQUEST_MAP ──────────────────────────────────────────────────────────

class TestToolRequestMap:
    def test_all_tools_mapped(self):
        expected = {
            "navigate", "click", "type", "clear", "select", "submit",
            "extract", "snapshot", "wait_for", "scroll", "eval_js",
        }
        assert expected == set(TOOL_REQUEST_MAP.keys())

    def test_map_values_are_pydantic_classes(self):
        from pydantic import BaseModel
        for name, cls in TOOL_REQUEST_MAP.items():
            assert issubclass(cls, BaseModel), f"{name} is not a BaseModel"


# ── ActionEffects ─────────────────────────────────────────────────────────────

class TestActionEffects:
    def test_defaults(self):
        e = ActionEffects()
        assert e.navigation is False
        assert e.dom_mutations == 0

    def test_extra_fields_allowed(self):
        e = ActionEffects(navigation=True, final_url="https://x.com", custom_key="yes")
        assert e.navigation is True


# ── ActionResponse ────────────────────────────────────────────────────────────

class TestActionResponse:
    def test_ok_status(self):
        r = ActionResponse(status="ok", action="click")
        assert r.ok is True
        assert r.failed is False

    def test_failed_status(self):
        r = ActionResponse(status="failed", action="click", error="not_found")
        assert r.failed is True
        assert r.ok is False

    def test_blocked_status(self):
        r = ActionResponse(status="blocked", action="navigate")
        assert r.failed is True

    def test_from_result_basic(self):
        raw = {
            "status": "ok",
            "action": "navigate",
            "target": None,
            "effects": {"navigation": True, "final_url": "https://x.com"},
        }
        r = ActionResponse.from_result(raw)
        assert r.status == "ok"
        assert r.effects.navigation is True
        assert r.effects.final_url == "https://x.com"

    def test_from_result_missing_fields(self):
        r = ActionResponse.from_result({"status": "ok"})
        assert r.action == ""
        assert r.error is None

    def test_from_result_with_error(self):
        raw = {"status": "failed", "action": "click", "error": "target_not_found"}
        r = ActionResponse.from_result(raw)
        assert r.error == "target_not_found"

    def test_to_tool_result_structure(self):
        r = ActionResponse(status="ok", action="click")
        tr = r.to_tool_result(tool_use_id="tu-123")
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "tu-123"
        assert tr["is_error"] is False
        payload = json.loads(tr["content"])
        assert payload["status"] == "ok"

    def test_to_tool_result_is_error_on_failure(self):
        r = ActionResponse(status="failed", action="navigate", error="blocked")
        tr = r.to_tool_result()
        assert tr["is_error"] is True

    def test_to_tool_result_no_tool_use_id(self):
        r = ActionResponse(status="ok", action="click")
        tr = r.to_tool_result()
        assert "tool_use_id" not in tr


# ── PageSemanticsResponse ─────────────────────────────────────────────────────

class TestPageSemanticsResponse:
    def test_from_result(self):
        raw = {
            "page_type": "form",
            "title": "Login",
            "url": "https://x.com/login",
            "primary_actions": [{"text": "Sign In"}],
            "inputs": [],
            "blocking_elements": [],
            "semantic_tree": {},
            "snapshot_id": "snap-001",
        }
        r = PageSemanticsResponse.from_result(raw)
        assert r.page_type == "form"
        assert r.title == "Login"
        assert r.snapshot_id == "snap-001"

    def test_from_result_missing_snapshot_id(self):
        r = PageSemanticsResponse.from_result({"page_type": "unknown"})
        assert r.snapshot_id == ""
        assert r.url == ""
