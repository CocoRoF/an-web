"""Regression tests for the SPA execution pipeline.

These lock in the shim capabilities that a real framework bootstrap
requires (discovered by walking Next.js/React through the engine):

- AbortController / PromiseRejectionEvent / real UTF-8 TextEncoder —
  their absence killed webpack runtimes or activated broken core-js
  Promise polyfills.
- A queue-backed ReadableStream — React's RSC flight client reads its
  payload through one; a stub that returns done immediately drops it.
- document.readyState lifecycle — frameworks finalise bootstrap at
  DOMContentLoaded.
- createElementNS — React renders SVG through it.
- Dynamic <script> load events — webpack's chunk loader resolves its
  Promises from them.
- Console capture — page errors must be observable.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from an_web import ANWebEngine
from an_web.browser.parser import parse_html
from an_web.js.runtime import JSRuntime

pytestmark = pytest.mark.asyncio


def _make_runtime():
    doc = parse_html(
        "<html><head><title>T</title></head><body><div id='m'>x</div></body></html>",
        "https://example.com/",
    )

    class Sess:
        _current_url = "https://example.com/"
        _current_document = doc
        _history: list = []

    return JSRuntime(Sess())


@pytest.fixture
def rt():
    runtime = _make_runtime()
    yield runtime
    runtime.close()


class TestWebAPIs:
    def test_abort_controller(self, rt):
        r = rt.eval_safe(
            "var c = new AbortController();"
            "var fired = false;"
            "c.signal.addEventListener('abort', function() { fired = true; });"
            "c.abort();"
            "c.signal.aborted && fired"
        )
        assert r.value is True

    def test_promise_rejection_event_exists(self, rt):
        assert rt.eval_safe("typeof PromiseRejectionEvent").value == "function"

    def test_text_encoder_utf8_roundtrip(self, rt):
        r = rt.eval_safe(
            "var enc = new TextEncoder();"
            "var dec = new TextDecoder();"
            "var s = '트랜스포머 한글 テスト ✓';"
            "dec.decode(enc.encode(s)) === s"
        )
        assert r.value is True

    def test_text_encoder_utf8_byte_lengths(self, rt):
        # Korean syllables are 3 bytes in UTF-8 — the old charCode
        # passthrough broke React's byte-offset stream parsing.
        assert rt.eval_safe("new TextEncoder().encode('한').length").value == 3
        assert rt.eval_safe("new TextEncoder().encode('a').length").value == 1

    def test_message_channel(self, rt):
        rt.eval_safe(
            "var got = null;"
            "var mc = new MessageChannel();"
            "mc.port1.onmessage = function(e) { got = e.data; };"
            "mc.port2.postMessage('hi');"
        )
        # postMessage delivers via a timer tick
        rt.eval_safe("_fireReadyTimers()")
        assert rt.eval_safe("got").value == "hi"

    def test_create_element_ns(self, rt):
        r = rt.eval_safe(
            "var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');"
            "svg.localName === 'svg' && svg.namespaceURI === 'http://www.w3.org/2000/svg'"
        )
        assert r.value is True


class TestReadableStream:
    @pytest.mark.asyncio
    async def test_enqueued_chunks_are_readable(self, rt):
        rt.eval_safe(
            "var out = [];"
            "var ctrl;"
            "var stream = new ReadableStream({start: function(c) { ctrl = c; }});"
            "var reader = stream.getReader();"
            "function pump() {"
            "  reader.read().then(function(r) {"
            "    if (r.done) { out.push('DONE'); return; }"
            "    out.push(r.value); pump();"
            "  });"
            "}"
            "pump();"
            "ctrl.enqueue('a'); ctrl.enqueue('b'); ctrl.close();"
        )
        await rt.drain_microtasks()
        assert rt.eval_safe("JSON.stringify(out)").value == '["a","b","DONE"]'

    @pytest.mark.asyncio
    async def test_queued_before_read(self, rt):
        rt.eval_safe(
            "var s2 = new ReadableStream({start: function(c) {"
            "  c.enqueue('x'); c.close();"
            "}});"
            "var got2 = null;"
            "s2.getReader().read().then(function(r) { got2 = r.value; });"
        )
        await rt.drain_microtasks()
        assert rt.eval_safe("got2").value == "x"


class TestConsoleCapture:
    def test_error_with_stack_is_captured(self, rt):
        rt.eval_safe("try { null.x } catch(e) { console.error(e); }")
        msgs = rt.eval_safe("_getConsoleMessages('error')").value
        assert "TypeError" in msgs

    def test_levels_filtered(self, rt):
        rt.eval_safe("console.log('L'); console.warn('W'); console.error('E');")
        assert "L" not in rt.eval_safe("_getConsoleMessages('error')").value
        assert "E" in rt.eval_safe("_getConsoleMessages('error')").value


class TestSPARenderPipeline:
    @respx.mock
    async def test_spa_fetch_render_reaches_snapshot(self):
        """The full loop: page script fetches JSON, renders it into the
        DOM, and the content is visible to extract/snapshot."""
        html = b"""<html><head><title>SPA</title></head><body>
        <div id="list"></div>
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            fetch('/api/items').then(function(r) { return r.json(); }).then(function(items) {
                var box = document.getElementById('list');
                for (var i = 0; i < items.length; i++) {
                    var h = document.createElement('h3');
                    h.textContent = items[i].title;
                    box.appendChild(h);
                }
            });
        });
        </script>
        </body></html>"""
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=html, headers={"content-type": "text/html"})
        )
        respx.get("https://example.com/api/items").mock(
            return_value=httpx.Response(200, json=[{"title": "문서 1"}, {"title": "문서 2"}])
        )
        async with ANWebEngine() as engine:
            session = await engine.create_session()
            await session.navigate("https://example.com/")
            ext = await session.act({"tool": "extract", "query": "#list h3"})
            texts = [r["text"] for r in ext["effects"]["results"]]
            assert texts == ["문서 1", "문서 2"]
            snap = await session.snapshot()
            assert snap.semantic_tree.find_by_text("문서 1")



class TestObserversFire:
    """IntersectionObserver/ResizeObserver must invoke callbacks (v0.9.1).

    Inert stubs left every lazy-loaded block on the modern web unrendered —
    the callback that mounts content never ran (naver.com portal blocks).
    """

    async def test_intersection_observer_fires_with_visible_entry(self, rt):
        rt.eval_safe("""
            window.__io_result = null;
            var el = document.createElement('div');
            new IntersectionObserver(function(entries, obs) {
                window.__io_result = {
                    n: entries.length,
                    isIntersecting: entries[0].isIntersecting,
                    ratio: entries[0].intersectionRatio
                };
            }).observe(el);
        """)
        for _ in range(3):
            await rt.drain_microtasks()
        r = rt.eval_safe("JSON.stringify(window.__io_result)")
        import json
        assert json.loads(r.value) == {"n": 1, "isIntersecting": True, "ratio": 1}

    async def test_resize_observer_fires_with_content_rect(self, rt):
        rt.eval_safe("""
            window.__ro_result = null;
            var el = document.createElement('div');
            new ResizeObserver(function(entries) {
                window.__ro_result = {
                    n: entries.length,
                    hasRect: !!entries[0].contentRect,
                    hasBox: entries[0].borderBoxSize.length === 1
                };
            }).observe(el);
        """)
        for _ in range(3):
            await rt.drain_microtasks()
        r = rt.eval_safe("JSON.stringify(window.__ro_result)")
        import json
        assert json.loads(r.value) == {"n": 1, "hasRect": True, "hasBox": True}

    async def test_disconnect_prevents_callback(self, rt):
        rt.eval_safe("""
            window.__io_calls = 0;
            var el = document.createElement('div');
            var obs = new IntersectionObserver(function() { window.__io_calls++; });
            obs.observe(el);
            obs.disconnect();
        """)
        for _ in range(3):
            await rt.drain_microtasks()
        assert rt.eval_safe("window.__io_calls").value == 0
