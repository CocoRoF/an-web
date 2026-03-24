"""Unit tests for Session and ANWebEngine."""
from __future__ import annotations

import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine
from an_web.core.session import Session
from an_web.policy.rules import PolicyRules


# ─── ANWebEngine ──────────────────────────────────────────────────────────────

class TestANWebEngine:
    @pytest.mark.asyncio
    async def test_create_session_returns_session(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert isinstance(session, Session)
            await session.close()

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self):
        async with ANWebEngine() as engine:
            s1 = await engine.create_session()
            s2 = await engine.create_session()
            assert s1 is not s2
            assert s1.cookies is not s2.cookies
            assert s1.network is not s2.network
            await s1.close()
            await s2.close()

    @pytest.mark.asyncio
    async def test_engine_close_closes_all_sessions(self):
        engine = ANWebEngine()
        s1 = await engine.create_session()
        s2 = await engine.create_session()
        await engine.close()
        assert s1._closed is True
        assert s2._closed is True

    @pytest.mark.asyncio
    async def test_custom_policy_respected(self):
        policy = PolicyRules(denied_domains=["blocked.com"])
        async with ANWebEngine() as engine:
            session = await engine.create_session(policy=policy)
            assert session.policy.denied_domains == ["blocked.com"]
            await session.close()

    @pytest.mark.asyncio
    async def test_engine_context_manager(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.network is not None
            # engine auto-closes on exit


# ─── Session initialization ───────────────────────────────────────────────────

class TestSessionInit:
    @pytest.mark.asyncio
    async def test_subsystems_initialized(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.scheduler is not None
            assert session.network is not None
            assert session.cookies is not None
            assert session.snapshots is not None
            await session.close()

    @pytest.mark.asyncio
    async def test_initial_url_is_about_blank(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.current_url == "about:blank"
            await session.close()

    @pytest.mark.asyncio
    async def test_initial_document_is_none(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session._current_document is None
            await session.close()

    @pytest.mark.asyncio
    async def test_history_starts_empty(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            assert session.history == []
            await session.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.close()
            await session.close()  # Should not raise


# ─── Session.navigate() ───────────────────────────────────────────────────────

class TestSessionNavigate:
    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_success(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><head><title>Home</title></head><body><p>Hi</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.navigate("https://example.com/")

        assert result["status"] == "ok"
        assert result["action"] == "navigate"

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_updates_url(self):
        respx.get("https://example.com/page").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><body>page</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/page")
                assert session.current_url == "https://example.com/page"

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_builds_document(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><body><button id='btn'>Click</button></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                assert session._current_document is not None
                btn = session._current_document.get_element_by_id("btn")
                assert btn is not None

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_appends_history(self):
        respx.get("https://example.com/a").mock(
            return_value=httpx.Response(200, content=b"<html></html>",
                                        headers={"content-type": "text/html"})
        )
        respx.get("https://example.com/b").mock(
            return_value=httpx.Response(200, content=b"<html></html>",
                                        headers={"content-type": "text/html"})
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/a")
                await session.navigate("https://example.com/b")
                assert "https://example.com/a" in session.history
                assert "https://example.com/b" in session.history

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_failed_404_returns_dict(self):
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404, content=b"Not found")
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.navigate("https://example.com/missing")
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_navigate_blocked_domain(self):
        policy = PolicyRules(denied_domains=["blocked.com"])
        async with ANWebEngine() as engine:
            async with await engine.create_session(policy=policy) as session:
                result = await session.navigate("https://blocked.com/page")
        assert result["status"] == "failed"


# ─── Session.snapshot() ───────────────────────────────────────────────────────

class TestSessionSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_empty_session(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                page = await session.snapshot()
        assert page.page_type == "empty"
        assert page.url == "about:blank"

    @pytest.mark.asyncio
    @respx.mock
    async def test_snapshot_after_navigate(self):
        respx.get("https://example.com/login").mock(
            return_value=httpx.Response(
                200,
                content=b"""
                <html><head><title>Login</title></head>
                <body>
                  <form action="/auth">
                    <input type="email" name="email">
                    <input type="password" name="pw">
                    <button type="submit">Login</button>
                  </form>
                </body></html>
                """,
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/login")
                page = await session.snapshot()

        assert page.title == "Login"
        assert len(page.inputs) >= 2
        assert len(page.primary_actions) >= 1


# ─── Session.act() ────────────────────────────────────────────────────────────

class TestSessionAct:
    @pytest.mark.asyncio
    @respx.mock
    async def test_act_navigate(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200, content=b"<html><body>hi</body></html>",
                headers={"content-type": "text/html"}
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.act({"tool": "navigate", "url": "https://example.com/"})

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    @respx.mock
    async def test_act_click(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><body><button id='b1'>Click me</button></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({"tool": "click", "target": "#b1"})

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    @respx.mock
    async def test_act_type(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><body><input id='q' type='text'></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({
                    "tool": "type",
                    "target": "#q",
                    "text": "hello world",
                })

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_act_unknown_tool(self):
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                result = await session.act({"tool": "nonexistent_tool"})

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    @respx.mock
    async def test_act_snapshot_returns_dict(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"<html><head><title>T</title></head><body><p>x</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        async with ANWebEngine() as engine:
            async with await engine.create_session() as session:
                await session.navigate("https://example.com/")
                result = await session.act({"tool": "snapshot"})

        assert "pageType" in result or "page_type" in result or "status" in result
