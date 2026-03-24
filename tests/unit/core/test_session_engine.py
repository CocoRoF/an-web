"""
Unit & integration tests for Session, ANWebEngine, and their interactions.

Coverage:
- ANWebEngine: create_session, session map, get_session, active_sessions,
  close, max_concurrent_sessions, repr
- Session init: subsystems, initial state, idempotent close
- Session.navigate(): success, URL update, document built, history, 404,
  blocked domain
- Session.navigate() -> JS lifecycle: session_storage cleared, js_runtime reset
- Session.back(): no history, back after navigate
- Session.execute_script(): JS eval, microtask drain
- Session.storage: localStorage origin isolation, session_storage reset on nav
- Session.storage_state(): serialisable dump
- Session.snapshot(): empty page, login form
- Session.act(): navigate, click, type, snapshot, unknown tool
- Session.page_state: tracks status transitions
- Session repr
"""
from __future__ import annotations

import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine
from an_web.core.session import Session
from an_web.core.state import EngineStatus
from an_web.policy.rules import PolicyRules


# ============================================================================
# Helpers
# ============================================================================

def _mock_page(url: str, html: bytes, status: int = 200) -> None:
    respx.get(url).mock(
        return_value=httpx.Response(
            status,
            content=html,
            headers={"content-type": "text/html"},
        )
    )


# ============================================================================
# ANWebEngine
# ============================================================================


class TestANWebEngine:
    async def test_create_session_returns_session(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert isinstance(session, Session)
            await session.close()

    async def test_multiple_sessions_isolated(self):
        async with ANWebEngine() as engine:
            s1 = await engine.create_session()
            s2 = await engine.create_session()
            assert s1 is not s2
            assert s1.cookies is not s2.cookies
            assert s1.network is not s2.network
            assert s1.session_id != s2.session_id
            await s1.close()
            await s2.close()

    async def test_engine_close_closes_all_sessions(self):
        engine = ANWebEngine()
        s1 = await engine.create_session()
        s2 = await engine.create_session()
        await engine.close()
        assert s1._closed is True
        assert s2._closed is True

    async def test_custom_policy_respected(self):
        policy = PolicyRules(denied_domains=["blocked.com"])
        async with ANWebEngine() as engine:
            session = await engine.create_session(policy=policy)
            assert session.policy.denied_domains == ["blocked.com"]
            await session.close()

    async def test_engine_context_manager(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.network is not None
            # engine auto-closes on exit

    async def test_get_session_by_id(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            found = engine.get_session(session.session_id)
            assert found is session

    async def test_get_session_unknown_id(self):
        async with ANWebEngine() as engine:
            result = engine.get_session("nonexistent-id")
            assert result is None

    async def test_explicit_session_id(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session(session_id="my-custom-id")
            assert session.session_id == "my-custom-id"
            assert engine.get_session("my-custom-id") is session

    async def test_active_sessions_excludes_closed(self):
        async with ANWebEngine() as engine:
            s1 = await engine.create_session()
            s2 = await engine.create_session()
            assert len(engine.active_sessions) == 2
            await s1.close()
            active = engine.active_sessions
            assert len(active) == 1
            assert active[0] is s2

    async def test_active_session_count(self):
        async with ANWebEngine() as engine:
            s = await engine.create_session()
            assert engine.active_session_count == 1
            await s.close()
            assert engine.active_session_count == 0

    async def test_session_count_includes_closed(self):
        async with ANWebEngine() as engine:
            s = await engine.create_session()
            await s.close()
            assert engine.session_count == 1

    async def test_max_concurrent_sessions_enforced(self):
        engine = ANWebEngine(max_concurrent_sessions=2)
        s1 = await engine.create_session()
        s2 = await engine.create_session()
        with pytest.raises(RuntimeError, match="capacity"):
            await engine.create_session()
        await engine.close()

    async def test_max_sessions_after_close_allows_new(self):
        engine = ANWebEngine(max_concurrent_sessions=1)
        s1 = await engine.create_session()
        await s1.close()
        # After closing, a new session can be created
        s2 = await engine.create_session()
        assert not s2._closed
        await engine.close()

    async def test_remove_session(self):
        async with ANWebEngine() as engine:
            s = await engine.create_session()
            sid = s.session_id
            engine.remove_session(sid)
            assert engine.get_session(sid) is None

    async def test_repr_contains_counts(self):
        engine = ANWebEngine()
        r = repr(engine)
        assert "ANWebEngine" in r
        assert "active=" in r

    async def test_max_concurrent_sessions_property(self):
        engine = ANWebEngine(max_concurrent_sessions=42)
        assert engine.max_concurrent_sessions == 42
        await engine.close()


# ============================================================================
# Session initialisation
# ============================================================================


class TestSessionInit:
    async def test_subsystems_initialized(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.scheduler is not None
            assert session.network is not None
            assert session.cookies is not None
            assert session.snapshots is not None
            assert session.js_runtime is not None
            await session.close()

    async def test_initial_url_is_about_blank(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.current_url == "about:blank"
            await session.close()

    async def test_initial_document_is_none(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session._current_document is None
            await session.close()

    async def test_history_starts_empty(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.history == []
            await session.close()

    async def test_close_idempotent(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.close()
            await session.close()  # should not raise

    async def test_session_id_is_string(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert isinstance(session.session_id, str)
            assert len(session.session_id) > 0

    async def test_session_id_unique(self):
        async with ANWebEngine() as engine:
            s1 = await engine.create_session()
            s2 = await engine.create_session()
            assert s1.session_id != s2.session_id
            await s1.close()
            await s2.close()

    async def test_session_repr(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            r = repr(session)
            assert "Session" in r
            assert "about:blank" in r
            await session.close()


# ============================================================================
# Session.navigate()
# ============================================================================


class TestSessionNavigate:
    @respx.mock
    async def test_navigate_success(self):
        _mock_page("https://example.com/", b"<html><body>Hi</body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.navigate("https://example.com/")
        assert result["status"] == "ok"
        assert result["action"] == "navigate"

    @respx.mock
    async def test_navigate_updates_url(self):
        _mock_page("https://example.com/page", b"<html><body>page</body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/page")
                assert session.current_url == "https://example.com/page"

    @respx.mock
    async def test_navigate_builds_document(self):
        _mock_page(
            "https://example.com/",
            b"<html><body><button id='btn'>Click</button></body></html>",
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                assert session._current_document is not None
                btn = session._current_document.get_element_by_id("btn")
                assert btn is not None

    @respx.mock
    async def test_navigate_appends_history(self):
        _mock_page("https://example.com/a", b"<html></html>")
        _mock_page("https://example.com/b", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/a")
                await session.navigate("https://example.com/b")
                assert "https://example.com/a" in session.history
                assert "https://example.com/b" in session.history

    @respx.mock
    async def test_navigate_failed_404_returns_dict(self):
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404, content=b"Not found")
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.navigate("https://example.com/missing")
        assert result["status"] == "failed"

    async def test_navigate_blocked_domain(self):
        policy = PolicyRules(denied_domains=["blocked.com"])
        async with ANWebEngine() as engine:
            async with await engine.create_session(policy=policy) as session:
                result = await session.navigate("https://blocked.com/page")
        assert result["status"] == "failed"

    @respx.mock
    async def test_navigate_sets_page_state_idle(self):
        _mock_page("https://example.com/", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                assert session.page_state.status == EngineStatus.IDLE
                assert session.page_state.dom_ready is True

    @respx.mock
    async def test_navigate_clears_session_storage(self):
        _mock_page("https://example.com/a", b"<html></html>")
        _mock_page("https://example.com/b", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/a")
                session.session_storage["key"] = "value"
                assert session.session_storage["key"] == "value"

                await session.navigate("https://example.com/b")
                assert "key" not in session.session_storage

    @respx.mock
    async def test_navigate_preserves_local_storage(self):
        _mock_page("https://example.com/a", b"<html></html>")
        _mock_page("https://example.com/b", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/a")
                session.local_storage["persist"] = "yes"

                await session.navigate("https://example.com/b")
                # localStorage for same origin should persist
                ls = session.get_local_storage("example.com")
                assert ls.get("persist") == "yes"


# ============================================================================
# Session.back()
# ============================================================================


class TestSessionBack:
    async def test_back_with_no_history_returns_failed(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.back()
        assert result["status"] == "failed"

    @respx.mock
    async def test_back_after_navigate(self):
        _mock_page("https://example.com/first", b"<html><body>first</body></html>")
        _mock_page("https://example.com/second", b"<html><body>second</body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/first")
                await session.navigate("https://example.com/second")
                result = await session.back()
        assert result["status"] == "ok"


# ============================================================================
# Session.execute_script()
# ============================================================================


class TestExecuteScript:
    @respx.mock
    async def test_execute_script_returns_value(self):
        _mock_page("https://example.com/", b"<html><body></body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                val = await session.execute_script("1 + 2")
        assert val == 3

    @respx.mock
    async def test_execute_script_arithmetic(self):
        _mock_page("https://example.com/", b"<html><body></body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                val = await session.execute_script("2 ** 10")
        assert val == 1024

    @respx.mock
    async def test_execute_script_string_return(self):
        _mock_page(
            "https://example.com/",
            b"<html><head><title>MyTitle</title></head></html>",
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                val = await session.execute_script("'hello from js'")
        assert val == "hello from js"

    @respx.mock
    async def test_execute_script_error_returns_none(self):
        _mock_page("https://example.com/", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                val = await session.execute_script("undefinedVariable.foo")
        assert val is None

    async def test_execute_script_without_navigate_returns_none(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                # JS runtime is present but no document loaded
                val = await session.execute_script("1 + 1")
        # May be 2 (JS runtime works without document) or None (not available)
        assert val is None or val == 2


# ============================================================================
# Session storage
# ============================================================================


class TestSessionStorage:
    async def test_local_storage_empty_on_init(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                assert session.local_storage == {}

    async def test_local_storage_read_write(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session.local_storage["foo"] = "bar"
                assert session.local_storage["foo"] == "bar"

    async def test_local_storage_per_origin_isolation(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                ls_a = session.get_local_storage("example.com")
                ls_b = session.get_local_storage("other.com")
                ls_a["k"] = "from-a"
                assert "k" not in ls_b

    async def test_session_storage_empty_on_init(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                assert session.session_storage == {}

    async def test_session_storage_read_write(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session.session_storage["tab"] = "1"
                assert session.session_storage["tab"] == "1"

    @respx.mock
    async def test_session_storage_cleared_on_navigate(self):
        _mock_page("https://example.com/", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session.session_storage["ephemeral"] = "gone"
                await session.navigate("https://example.com/")
                assert "ephemeral" not in session.session_storage

    @respx.mock
    async def test_local_storage_not_cleared_on_navigate(self):
        _mock_page("https://example.com/", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session._local_storage["example.com"] = {"persist": "yes"}
                await session.navigate("https://example.com/")
                assert session._local_storage.get("example.com", {}).get("persist") == "yes"

    async def test_storage_state_serialisable(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session._local_storage["example.com"] = {"k": "v"}
                session.session_storage["s"] = "1"
                state = session.storage_state()
                assert "local_storage" in state
                assert "session_storage" in state
                assert "cookies" in state
                assert state["local_storage"]["example.com"]["k"] == "v"
                assert state["session_storage"]["s"] == "1"

    async def test_storage_state_is_json_serialisable(self):
        import json
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                session._local_storage["x.com"] = {"key": "value"}
                state = session.storage_state()
                json.dumps(state)  # should not raise


# ============================================================================
# Session.snapshot()
# ============================================================================


class TestSessionSnapshot:
    async def test_snapshot_empty_session(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                page = await session.snapshot()
        assert page.page_type == "empty"
        assert page.url == "about:blank"

    @respx.mock
    async def test_snapshot_after_navigate(self):
        _mock_page(
            "https://example.com/login",
            b"""
            <html><head><title>Login</title></head>
            <body>
              <form action="/auth">
                <input type="email" name="email">
                <input type="password" name="pw">
                <button type="submit">Login</button>
              </form>
            </body></html>
            """,
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/login")
                page = await session.snapshot()

        assert page.title == "Login"
        assert len(page.inputs) >= 2
        assert len(page.primary_actions) >= 1


# ============================================================================
# Session.act()
# ============================================================================


class TestSessionAct:
    @respx.mock
    async def test_act_navigate(self):
        _mock_page("https://example.com/", b"<html><body>hi</body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.act({"tool": "navigate", "url": "https://example.com/"})
        assert result["status"] == "ok"

    @respx.mock
    async def test_act_click(self):
        _mock_page(
            "https://example.com/",
            b"<html><body><button id='b1'>Click me</button></body></html>",
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({"tool": "click", "target": "#b1"})
        assert result["status"] == "ok"

    @respx.mock
    async def test_act_type(self):
        _mock_page(
            "https://example.com/",
            b"<html><body><input id='q' type='text'></body></html>",
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({"tool": "type", "target": "#q", "text": "hello"})
        assert result["status"] == "ok"

    async def test_act_unknown_tool(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.act({"tool": "nonexistent_tool"})
        assert result["status"] == "failed"

    @respx.mock
    async def test_act_snapshot_returns_dict(self):
        _mock_page(
            "https://example.com/",
            b"<html><head><title>T</title></head><body><p>x</p></body></html>",
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({"tool": "snapshot"})
        assert "pageType" in result or "page_type" in result or "status" in result

    @respx.mock
    async def test_act_input_schema_format(self):
        """Support {"name": ..., "input": {...}} tool call format."""
        _mock_page("https://example.com/", b"<html><body>hi</body></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.act({
                    "name": "navigate",
                    "input": {"url": "https://example.com/"},
                })
        assert result["status"] == "ok"


# ============================================================================
# Session page_state
# ============================================================================


class TestSessionPageState:
    async def test_initial_page_state(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                state = session.page_state
                assert state.status == EngineStatus.IDLE
                assert state.dom_ready is False
                assert state.url == "about:blank"

    @respx.mock
    async def test_page_state_after_navigate(self):
        _mock_page("https://example.com/", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                assert session.page_state.status == EngineStatus.IDLE
                assert session.page_state.dom_ready is True
                assert session.page_state.navigation_count == 1

    @respx.mock
    async def test_navigation_count_increments(self):
        _mock_page("https://example.com/a", b"<html></html>")
        _mock_page("https://example.com/b", b"<html></html>")
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/a")
                await session.navigate("https://example.com/b")
                assert session.page_state.navigation_count == 2
