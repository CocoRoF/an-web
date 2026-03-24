"""Unit tests for StructuredLogger, LogRecord, ActionLogger."""
from __future__ import annotations

import pytest
from an_web.tracing.logs import (
    StructuredLogger, LogLevel, LogRecord, ActionLogger, get_logger,
)


# ── LogLevel ──────────────────────────────────────────────────────────────────

class TestLogLevel:
    def test_values(self):
        assert LogLevel.DEBUG == "debug"
        assert LogLevel.INFO == "info"
        assert LogLevel.WARNING == "warning"
        assert LogLevel.ERROR == "error"
        assert LogLevel.CRITICAL == "critical"


# ── LogRecord ─────────────────────────────────────────────────────────────────

class TestLogRecord:
    def test_to_dict_keys(self):
        rec = LogRecord(
            record_id="log-abc",
            level="info",
            message="hello",
            timestamp=1000.0,
            session_id="s1",
        )
        d = rec.to_dict()
        assert d["record_id"] == "log-abc"
        assert d["level"] == "info"
        assert d["message"] == "hello"
        assert d["session_id"] == "s1"

    def test_action_id_omitted_when_none(self):
        rec = LogRecord(
            record_id="r1", level="info", message="x",
            timestamp=0.0, session_id="s",
        )
        d = rec.to_dict()
        assert "action_id" not in d

    def test_action_id_included_when_set(self):
        rec = LogRecord(
            record_id="r1", level="info", message="x",
            timestamp=0.0, session_id="s", action_id="act-1",
        )
        assert rec.to_dict()["action_id"] == "act-1"

    def test_data_included(self):
        rec = LogRecord(
            record_id="r1", level="error", message="x",
            timestamp=0.0, session_id="s",
            data={"url": "https://x.com"},
        )
        assert rec.to_dict()["data"]["url"] == "https://x.com"


# ── StructuredLogger basic ────────────────────────────────────────────────────

class TestStructuredLoggerBasic:
    def test_starts_empty(self):
        sl = StructuredLogger("test", "s1")
        assert len(sl) == 0

    def test_log_returns_record(self):
        sl = StructuredLogger("test", "s1")
        rec = sl.log(LogLevel.INFO, "hello")
        assert isinstance(rec, LogRecord)
        assert rec.level == "info"
        assert rec.message == "hello"

    def test_log_stores_record(self):
        sl = StructuredLogger("test", "s1")
        sl.log(LogLevel.INFO, "a")
        sl.log(LogLevel.INFO, "b")
        assert len(sl) == 2

    def test_debug_info_warning_error_critical(self):
        sl = StructuredLogger("test", "s1")
        sl.debug("d")
        sl.info("i")
        sl.warning("w")
        sl.error("e")
        sl.critical("c")
        levels = [r.level for r in sl.get_all()]
        assert levels == ["debug", "info", "warning", "error", "critical"]

    def test_session_id_on_record(self):
        sl = StructuredLogger("test", "my-session")
        rec = sl.info("x")
        assert rec.session_id == "my-session"

    def test_data_attached(self):
        sl = StructuredLogger("test", "s1")
        rec = sl.info("x", data={"key": "value"})
        assert rec.data["key"] == "value"

    def test_action_id_attached(self):
        sl = StructuredLogger("test", "s1")
        rec = sl.info("x", action_id="act-99")
        assert rec.action_id == "act-99"


# ── Ring buffer ───────────────────────────────────────────────────────────────

class TestRingBuffer:
    def test_max_size_evicts_oldest(self):
        sl = StructuredLogger("test", "s1", max_size=3)
        sl.info("a")
        sl.info("b")
        sl.info("c")
        sl.info("d")
        records = sl.get_all()
        assert len(records) == 3
        assert records[0].message == "b"

    def test_unlimited_when_zero(self):
        sl = StructuredLogger("test", "s1", max_size=0)
        for i in range(500):
            sl.info(str(i))
        assert len(sl) == 500


# ── Queries ───────────────────────────────────────────────────────────────────

class TestQueries:
    def test_get_by_level(self):
        sl = StructuredLogger("test", "s1")
        sl.info("a")
        sl.error("b")
        sl.info("c")
        infos = sl.get_by_level("info")
        assert len(infos) == 2
        errors = sl.get_by_level(LogLevel.ERROR)
        assert len(errors) == 1

    def test_get_by_action(self):
        sl = StructuredLogger("test", "s1")
        sl.info("a", action_id="act-1")
        sl.info("b", action_id="act-2")
        sl.info("c", action_id="act-1")
        acts = sl.get_by_action("act-1")
        assert len(acts) == 2

    def test_get_errors(self):
        sl = StructuredLogger("test", "s1")
        sl.info("ok")
        sl.error("bad")
        sl.critical("very bad")
        errs = sl.get_errors()
        assert len(errs) == 2

    def test_clear(self):
        sl = StructuredLogger("test", "s1")
        sl.info("a")
        sl.info("b")
        sl.clear()
        assert len(sl) == 0


# ── Action context manager ────────────────────────────────────────────────────

class TestActionContext:
    def test_action_id_bound_in_context(self):
        sl = StructuredLogger("test", "s1")
        with sl.action_context("act-42"):
            rec = sl.info("inside context")
        assert rec.action_id == "act-42"

    def test_action_id_restored_after_context(self):
        sl = StructuredLogger("test", "s1")
        sl._set_action_id("outer")
        with sl.action_context("inner"):
            sl.info("inside")
        rec = sl.info("outside")
        assert rec.action_id == "outer"

    def test_nested_contexts(self):
        sl = StructuredLogger("test", "s1")
        with sl.action_context("outer"):
            with sl.action_context("inner"):
                rec = sl.info("deep")
            rec_outer = sl.info("back-outer")
        assert rec.action_id == "inner"
        assert rec_outer.action_id == "outer"

    def test_context_returns_logger(self):
        sl = StructuredLogger("test", "s1")
        with sl.action_context("a") as logger:
            assert logger is sl


# ── ActionLogger backward compat ──────────────────────────────────────────────

class TestActionLogger:
    def test_log_and_get_events(self):
        al = ActionLogger()
        al.log("navigate", {"url": "https://x.com"})
        events = al.get_events()
        assert len(events) == 1
        assert events[0]["type"] == "navigate"
        assert events[0]["url"] == "https://x.com"
        assert "timestamp" in events[0]

    def test_multiple_events(self):
        al = ActionLogger()
        al.log("click", {"target": "#btn"})
        al.log("type", {"value": "hello"})
        assert len(al.get_events()) == 2

    def test_structured_property(self):
        al = ActionLogger("my-session")
        assert isinstance(al.structured, StructuredLogger)


# ── get_logger factory ────────────────────────────────────────────────────────

class TestGetLogger:
    def test_returns_structured_logger(self):
        sl = get_logger("actions")
        assert isinstance(sl, StructuredLogger)

    def test_session_id_passed(self):
        sl = get_logger("actions", session_id="sess-99")
        rec = sl.info("test")
        assert rec.session_id == "sess-99"

    def test_repr(self):
        sl = StructuredLogger("my-logger", "s1")
        r = repr(sl)
        assert "my-logger" in r
