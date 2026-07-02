"""Tests for the agent data plane: fetch tool, network tool, async
fetch/XHR bridge processing, and eval_js Promise awaiting."""
from __future__ import annotations

import httpx
import pytest
import respx

from an_web import ANWebEngine

pytestmark = pytest.mark.asyncio


def _mock_page(url: str, html: bytes, status: int = 200) -> None:
    respx.get(url).mock(
        return_value=httpx.Response(
            status, content=html, headers={"content-type": "text/html"}
        )
    )


_PAGE = b"<html><head><title>T</title></head><body><h1>Hello</h1></body></html>"


class TestFetchTool:
    @respx.mock
    async def test_fetch_returns_parsed_json(self):
        _mock_page("https://example.com/", _PAGE)
        respx.get("https://example.com/api/items").mock(
            return_value=httpx.Response(
                200, json={"items": [1, 2, 3]},
            )
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({"tool": "fetch", "url": "/api/items"})
            assert res["status"] == "ok"
            assert res["effects"]["status"] == 200
            assert res["effects"]["json"] == {"items": [1, 2, 3]}

    @respx.mock
    async def test_fetch_relative_url_resolves_against_page(self):
        _mock_page("https://example.com/app/", _PAGE)
        route = respx.get("https://example.com/app/data").mock(
            return_value=httpx.Response(200, text="ok")
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/app/")
            res = await session.act({"tool": "fetch", "url": "data"})
            assert res["status"] == "ok"
            assert route.called

    @respx.mock
    async def test_fetch_respects_policy(self):
        from an_web.policy.rules import PolicyRules
        _mock_page("https://example.com/", _PAGE)
        async with ANWebEngine() as engine:
            session = await engine.create_session(
                policy=PolicyRules.sandboxed(allowed_domains=["example.com"])
            )
            await session.navigate("https://example.com/")
            res = await session.act({"tool": "fetch", "url": "https://evil.com/x"})
            assert res["status"] == "failed"

    @respx.mock
    async def test_fetch_records_network_log(self):
        _mock_page("https://example.com/", _PAGE)
        respx.get("https://example.com/api").mock(
            return_value=httpx.Response(200, text="data")
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            await session.act({"tool": "fetch", "url": "/api"})
            res = await session.act({"tool": "network"})
            assert res["effects"]["count"] == 1
            entry = res["effects"]["requests"][0]
            assert entry["kind"] == "agent"
            assert entry["body"] == "data"


class TestPageFetchBridge:
    @respx.mock
    async def test_page_fetch_resolves_during_settle(self):
        html = b"""<html><head><title>T</title></head><body>
        <div id="out"></div>
        <script>
        fetch('/api/msg').then(function(r) { return r.text(); }).then(function(t) {
            document.getElementById('out').textContent = t;
        });
        </script>
        </body></html>"""
        _mock_page("https://example.com/", html)
        respx.get("https://example.com/api/msg").mock(
            return_value=httpx.Response(200, text="loaded-by-js")
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            ext = await session.act({"tool": "extract", "query": "#out"})
            results = ext["effects"]["results"]
            assert results and results[0]["text"] == "loaded-by-js"

    @respx.mock
    async def test_page_xhr_fires_onload(self):
        html = b"""<html><head><title>T</title></head><body>
        <div id="out"></div>
        <script>
        var x = new XMLHttpRequest();
        x.open('GET', '/api/xhr');
        x.onload = function() {
            document.getElementById('out').textContent = 'xhr:' + x.status;
        };
        x.send();
        </script>
        </body></html>"""
        _mock_page("https://example.com/", html)
        respx.get("https://example.com/api/xhr").mock(
            return_value=httpx.Response(200, text="y")
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            ext = await session.act({"tool": "extract", "query": "#out"})
            assert ext["effects"]["results"][0]["text"] == "xhr:200"

    @respx.mock
    async def test_click_handler_fetch_settles(self):
        html = b"""<html><head><title>T</title></head><body>
        <button id="load">Load</button><div id="out"></div>
        <script>
        document.getElementById('load').addEventListener('click', function() {
            fetch('/api/clicked').then(function(r) { return r.text(); }).then(function(t) {
                document.getElementById('out').textContent = t;
            });
        });
        </script>
        </body></html>"""
        _mock_page("https://example.com/", html)
        respx.get("https://example.com/api/clicked").mock(
            return_value=httpx.Response(200, text="after-click")
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            await session.act({"tool": "click", "target": "#load"})
            ext = await session.act({"tool": "extract", "query": "#out"})
            assert ext["effects"]["results"][0]["text"] == "after-click"


class TestEvalJSPromise:
    @respx.mock
    async def test_promise_result_is_awaited(self):
        _mock_page("https://example.com/", _PAGE)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({
                "tool": "eval_js", "script": "Promise.resolve({a: 1})",
            })
            assert res["status"] == "ok"
            assert res["effects"]["awaited"] is True
            assert res["effects"]["raw_value"] == {"a": 1}

    @respx.mock
    async def test_awaited_fetch_returns_json(self):
        _mock_page("https://example.com/", _PAGE)
        respx.get("https://example.com/api/j").mock(
            return_value=httpx.Response(200, json={"v": 42})
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({
                "tool": "eval_js",
                "script": "fetch('/api/j').then(r => r.json())",
            })
            assert res["status"] == "ok"
            assert res["effects"]["raw_value"] == {"v": 42}

    @respx.mock
    async def test_rejected_promise_reports_error(self):
        _mock_page("https://example.com/", _PAGE)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({
                "tool": "eval_js",
                "script": "Promise.reject(new Error('boom'))",
            })
            assert res["status"] == "failed"
            assert "boom" in (res.get("error") or "")

    @respx.mock
    async def test_sync_script_still_works(self):
        _mock_page("https://example.com/", _PAGE)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({"tool": "eval_js", "script": "1 + 2"})
            assert res["status"] == "ok"
            assert res["effects"]["raw_value"] == 3


class TestElementIdentity:
    @respx.mock
    async def test_wrapper_expandos_survive_reaccess(self):
        _mock_page("https://example.com/", _PAGE)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            rt = session.js_runtime
            r = rt.eval_safe(
                "var a = document.querySelector('h1');"
                "a.__fiber = 'xyz';"
                "var b = document.querySelector('h1');"
                "(a === b) && b.__fiber === 'xyz'"
            )
            assert r.value is True


class TestHtmlExtractionHygiene:
    @respx.mock
    async def test_html_mode_strips_scripts(self):
        html = b"""<html><head><title>T</title></head><body>
        <main><p>Real content</p><script>var junk = 'NOISE';</script></main>
        </body></html>"""
        _mock_page("https://example.com/", html)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({
                "tool": "extract", "query": {"mode": "html", "selector": "main"},
            })
            out = res["effects"]["results"][0]["html"]
            assert "Real content" in out
            assert "NOISE" not in out

    @respx.mock
    async def test_css_mode_text_excludes_script(self):
        html = b"""<html><head><title>T</title></head><body>
        <div id="box">Visible<script>var hidden = 1;</script></div>
        </body></html>"""
        _mock_page("https://example.com/", html)
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            res = await session.act({"tool": "extract", "query": "#box"})
            text = res["effects"]["results"][0]["text"]
            assert "Visible" in text
            assert "hidden" not in text
