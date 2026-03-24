"""Unit tests for ArtifactCollector and Artifact."""
from __future__ import annotations

import json
import pytest
from an_web.tracing.artifacts import (
    Artifact, ArtifactCollector, ArtifactKind,
    _dom_payload, _semantic_payload, _network_request_payload,
    _js_exception_payload, _action_trace_payload, _policy_violation_payload,
    _extract_visible_text,
)


# ── ArtifactKind ──────────────────────────────────────────────────────────────

class TestArtifactKind:
    def test_values_are_strings(self):
        assert ArtifactKind.DOM_SNAPSHOT == "dom_snapshot"
        assert ArtifactKind.SEMANTIC_SNAPSHOT == "semantic_snapshot"
        assert ArtifactKind.NETWORK_TRACE == "network_trace"
        assert ArtifactKind.JS_EXCEPTION == "js_exception"
        assert ArtifactKind.ACTION_TRACE == "action_trace"
        assert ArtifactKind.POLICY_VIOLATION == "policy_violation"
        assert ArtifactKind.CUSTOM == "custom"


# ── Artifact dataclass ─────────────────────────────────────────────────────────

class TestArtifact:
    def _artifact(self, **kwargs) -> Artifact:
        defaults = dict(
            artifact_id="art-abc123",
            kind=ArtifactKind.DOM_SNAPSHOT,
            timestamp=1000.0,
            session_id="s1",
            data={"html": "<p>hi</p>", "url": "https://example.com"},
        )
        defaults.update(kwargs)
        return Artifact(**defaults)

    def test_content_hash_is_16_hex(self):
        a = self._artifact()
        assert len(a.content_hash) == 16
        assert all(c in "0123456789abcdef" for c in a.content_hash)

    def test_content_hash_stable(self):
        a = self._artifact()
        assert a.content_hash == a.content_hash

    def test_content_hash_different_data(self):
        a1 = self._artifact(data={"x": 1})
        a2 = self._artifact(data={"x": 2})
        assert a1.content_hash != a2.content_hash

    def test_to_dict_keys(self):
        a = self._artifact()
        d = a.to_dict()
        assert "artifact_id" in d
        assert "kind" in d
        assert "timestamp" in d
        assert "session_id" in d
        assert "data" in d
        assert "content_hash" in d

    def test_to_dict_optional_fields_omitted(self):
        a = self._artifact()
        d = a.to_dict()
        assert "action_id" not in d
        assert "url" not in d
        assert "metadata" not in d

    def test_to_dict_optional_fields_included(self):
        a = self._artifact(action_id="act-1", url="https://x.com", metadata={"k": "v"})
        d = a.to_dict()
        assert d["action_id"] == "act-1"
        assert d["url"] == "https://x.com"
        assert d["metadata"] == {"k": "v"}

    def test_to_json_valid(self):
        a = self._artifact()
        j = a.to_json()
        obj = json.loads(j)
        assert obj["artifact_id"] == "art-abc123"

    def test_from_dict_roundtrip(self):
        a = self._artifact(action_id="x", url="https://y.com", metadata={"z": 1})
        a2 = Artifact.from_dict(a.to_dict())
        assert a2.artifact_id == a.artifact_id
        assert a2.kind == a.kind
        assert a2.session_id == a.session_id
        assert a2.action_id == a.action_id
        assert a2.url == a.url
        assert a2.metadata == a.metadata


# ── Payload builders ──────────────────────────────────────────────────────────

class TestPayloadBuilders:
    def test_dom_payload(self):
        p = _dom_payload("<html><body>Hello world</body></html>", "https://x.com")
        assert p["html"].startswith("<html")
        assert p["url"] == "https://x.com"
        assert p["char_count"] > 0
        assert "Hello world" in p["visible_text_preview"]

    def test_semantic_payload(self):
        sem = {"page_type": "form", "title": "Login", "url": "https://x.com"}
        p = _semantic_payload(sem)
        assert p["page_type"] == "form"
        assert p["title"] == "Login"
        assert p["raw"] == sem

    def test_network_request_payload(self):
        p = _network_request_payload(
            "https://api.example.com", "POST", 200, 120.5,
            {"Authorization": "Bearer tok"}, {"Content-Type": "application/json"},
            '{"ok":true}',
        )
        assert p["url"] == "https://api.example.com"
        assert p["method"] == "POST"
        assert p["status"] == 200
        assert p["duration_ms"] == 120.5
        assert "tok" in p["request_headers"].get("Authorization", "")

    def test_network_body_truncated(self):
        p = _network_request_payload("https://x.com", "GET", 200, None, None, None, "x" * 3000)
        assert len(p["body_preview"]) == 2048

    def test_js_exception_payload(self):
        p = _js_exception_payload("ReferenceError: foo", "at eval:1", "https://x.com", 1, 5, "foo()")
        assert p["message"] == "ReferenceError: foo"
        assert p["line"] == 1
        assert p["col"] == 5

    def test_action_trace_payload(self):
        p = _action_trace_payload("click", "ok", "#btn", None, 45.0, [{"type": "dom_changed"}])
        assert p["action"] == "click"
        assert p["status"] == "ok"
        assert len(p["effects"]) == 1

    def test_policy_violation_payload(self):
        p = _policy_violation_payload(
            "navigate", "DOMAIN_DENIED", "blocked", "https://evil.com",
            {"host": "evil.com"},
        )
        assert p["violation_type"] == "DOMAIN_DENIED"
        assert p["url"] == "https://evil.com"


# ── _extract_visible_text ─────────────────────────────────────────────────────

class TestExtractVisibleText:
    def test_strips_tags(self):
        text = _extract_visible_text("<p>Hello <b>world</b></p>")
        assert "Hello" in text
        assert "world" in text
        assert "<" not in text

    def test_strips_script(self):
        text = _extract_visible_text("<div>visible</div><script>secret()</script>")
        assert "visible" in text
        assert "secret" not in text

    def test_strips_style(self):
        text = _extract_visible_text("<style>.x{color:red}</style><p>text</p>")
        assert "text" in text
        assert "color" not in text

    def test_collapses_whitespace(self):
        text = _extract_visible_text("<p>  a   b  </p>")
        assert "  " not in text


# ── ArtifactCollector basic ───────────────────────────────────────────────────

class TestArtifactCollectorBasic:
    def test_starts_empty(self):
        c = ArtifactCollector("s1")
        assert len(c) == 0
        assert c.get_all() == []

    def test_always_truthy(self):
        c = ArtifactCollector("s1")
        assert bool(c) is True

    def test_record_returns_artifact(self):
        c = ArtifactCollector("s1")
        a = c.record(ArtifactKind.CUSTOM, {"x": 1})
        assert isinstance(a, Artifact)
        assert a.kind == "custom"
        assert a.session_id == "s1"

    def test_artifact_id_unique(self):
        c = ArtifactCollector("s1")
        ids = {c.record(ArtifactKind.CUSTOM, {}).artifact_id for _ in range(20)}
        assert len(ids) == 20

    def test_record_dom(self):
        c = ArtifactCollector("s1")
        a = c.record_dom("<p>hi</p>", "https://x.com")
        assert a.kind == ArtifactKind.DOM_SNAPSHOT
        assert a.data["url"] == "https://x.com"
        assert len(c) == 1

    def test_record_semantic(self):
        c = ArtifactCollector("s1")
        a = c.record_semantic({"title": "Login"})
        assert a.kind == ArtifactKind.SEMANTIC_SNAPSHOT

    def test_record_network(self):
        c = ArtifactCollector("s1")
        a = c.record_network("https://api.x.com", "POST", status=201)
        assert a.kind == ArtifactKind.NETWORK_TRACE
        assert a.data["status"] == 201

    def test_record_js_exception(self):
        c = ArtifactCollector("s1")
        a = c.record_js_exception("TypeError", stack="at eval:5")
        assert a.kind == ArtifactKind.JS_EXCEPTION
        assert a.data["stack"] == "at eval:5"

    def test_record_action_trace(self):
        c = ArtifactCollector("s1")
        a = c.record_action_trace("click", "ok", target="#btn")
        assert a.kind == ArtifactKind.ACTION_TRACE
        assert a.data["action"] == "click"

    def test_record_policy_violation(self):
        c = ArtifactCollector("s1")
        a = c.record_policy_violation("navigate", "DOMAIN_DENIED", "blocked")
        assert a.kind == ArtifactKind.POLICY_VIOLATION
        assert a.data["violation_type"] == "DOMAIN_DENIED"


# ── ArtifactCollector queries ─────────────────────────────────────────────────

class TestArtifactCollectorQueries:
    def test_get_by_kind(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com")
        c.record_network("https://b.com")
        doms = c.get_by_kind(ArtifactKind.DOM_SNAPSHOT)
        assert len(doms) == 1
        assert doms[0].kind == ArtifactKind.DOM_SNAPSHOT

    def test_get_by_kind_string(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com")
        doms = c.get_by_kind("dom_snapshot")
        assert len(doms) == 1

    def test_get_by_action(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com", action_id="act-1")
        c.record_dom("<p/>", "https://b.com", action_id="act-2")
        acts = c.get_by_action("act-1")
        assert len(acts) == 1
        assert acts[0].action_id == "act-1"

    def test_get_failures(self):
        c = ArtifactCollector("s1")
        c.record_action_trace("click", "ok")
        c.record_action_trace("navigate", "failed", error="timeout")
        failures = c.get_failures()
        assert len(failures) == 1
        assert failures[0].data["error"] == "timeout"


# ── Ring buffer (max_size) ────────────────────────────────────────────────────

class TestRingBuffer:
    def test_max_size_evicts_oldest(self):
        c = ArtifactCollector("s1", max_size=3)
        for i in range(5):
            c.record(ArtifactKind.CUSTOM, {"i": i})
        assert len(c) == 3
        ids_i = [a.data["i"] for a in c.get_all()]
        assert ids_i == [2, 3, 4]

    def test_zero_max_size_unlimited(self):
        c = ArtifactCollector("s1", max_size=0)
        for _ in range(500):
            c.record(ArtifactKind.CUSTOM, {})
        assert len(c) == 500


# ── Export / import ───────────────────────────────────────────────────────────

class TestExportImport:
    def test_export_has_artifacts(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com")
        exp = c.export()
        assert exp["count"] == 1
        assert exp["session_id"] == "s1"
        assert len(exp["artifacts"]) == 1

    def test_export_json_valid(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com")
        j = c.export_json()
        obj = json.loads(j)
        assert obj["count"] == 1

    def test_from_export_roundtrip(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p>hello</p>", "https://a.com")
        c.record_network("https://api.com")
        exp = c.export()
        c2 = ArtifactCollector.from_export(exp)
        assert len(c2) == 2
        assert c2.get_all()[0].kind == ArtifactKind.DOM_SNAPSHOT

    def test_from_export_preserves_session_id(self):
        c = ArtifactCollector("my-session")
        exp = c.export()
        c2 = ArtifactCollector.from_export(exp)
        assert c2._session_id == "my-session"


# ── Summary ───────────────────────────────────────────────────────────────────

class TestSummary:
    def test_summary_empty(self):
        c = ArtifactCollector("s1")
        s = c.summary()
        assert s["total"] == 0
        assert s["by_kind"] == {}
        assert s["action_failures"] == 0

    def test_summary_counts(self):
        c = ArtifactCollector("s1")
        c.record_dom("<p/>", "https://a.com")
        c.record_dom("<p/>", "https://b.com")
        c.record_network("https://api.com")
        c.record_action_trace("click", "ok")
        c.record_action_trace("navigate", "failed")
        s = c.summary()
        assert s["total"] == 5
        assert s["by_kind"]["dom_snapshot"] == 2
        assert s["by_kind"]["network_trace"] == 1
        assert s["action_failures"] == 1

    def test_repr(self):
        c = ArtifactCollector("my-session")
        r = repr(c)
        assert "my-session" in r
        assert "count=0" in r
