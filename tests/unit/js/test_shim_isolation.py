"""Regression tests for V8 shim isolation and runaway-JS budgets.

These lock in the fixes for real-world failures observed on script-heavy
sites (Wikipedia/jQuery):

1. document.implementation.createHTMLDocument() must return an ISOLATED
   scratch document — jQuery support tests write into it and previously
   wiped the real <body>.
2. DOMParser.parseFromString() must not alias the real document.
3. document.write() must APPEND to <body>, never replace its contents.
4. Timer cascades (setTimeout(fn, 0) chains) must be budget-bounded so a
   single drain cannot block the engine for tens of seconds.
5. Mutation replay must refuse a catastrophic wipe of a large parsed
   <body>/<html>/<head> subtree.
6. Per-eval V8 timeouts must interrupt runaway scripts and leave the
   runtime usable.
"""
from __future__ import annotations

import time

import pytest

from an_web.browser.parser import parse_html
from an_web.dom.nodes import Element, TextNode
from an_web.js.host_api import _is_catastrophic_wipe, sync_dom_mutations
from an_web.js.runtime import JSRuntime

_PAGE = """<html><head><title>Iso Test</title></head><body>
<h1>Heading</h1>
<div id="main" class="container">
  <p class="text">Paragraph one</p>
  <p class="text">Paragraph two</p>
  <a href="/next">Next</a>
</div>
</body></html>"""


def _make_runtime(html: str = _PAGE, url: str = "https://example.com/"):
    doc = parse_html(html, url)

    class Sess:
        _current_url = url
        _current_document = doc
        _history: list = []

    return JSRuntime(Sess()), doc


def _count_tag(doc, tag: str) -> int:
    return sum(
        1 for n in doc.iter_descendants()
        if isinstance(n, Element) and n.tag == tag
    )


@pytest.fixture
def rt():
    runtime, doc = _make_runtime()
    yield runtime, doc
    runtime.close()


# ─────────────────────────────────────────────────────────────────────────────
# Scratch document isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestScratchDocumentIsolation:
    def test_create_html_document_is_not_real_document(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var d = document.implementation.createHTMLDocument('t');"
            "d.body.nodeId !== document.body.nodeId"
        )
        assert r.value is True

    def test_scratch_body_write_does_not_wipe_real_body(self, rt):
        runtime, doc = rt
        # The exact jQuery support-test pattern that wiped Wikipedia.
        r = runtime.eval_safe(
            "var d = document.implementation.createHTMLDocument('');"
            "d.body.innerHTML = '<form></form><form></form>';"
            "document.querySelectorAll('p').length"
        )
        assert r.value == 2
        sync_dom_mutations(runtime.ctx, runtime.session)
        assert _count_tag(doc, "p") == 2
        assert _count_tag(doc, "h1") == 1
        assert _count_tag(doc, "form") == 0

    def test_scratch_fragments_are_not_grafted_into_body(self, rt):
        runtime, doc = rt
        runtime.eval_safe(
            "var d = document.implementation.createHTMLDocument('');"
            "d.body.innerHTML = '<form></form><form></form>';"
        )
        sync_dom_mutations(runtime.ctx, runtime.session)
        assert _count_tag(doc, "form") == 0

    def test_dom_parser_returns_isolated_document(self, rt):
        runtime, doc = rt
        r = runtime.eval_safe(
            "var p = new DOMParser();"
            "var d = p.parseFromString('<div id=\"parsed\">x</div>', 'text/html');"
            "d !== document && document.getElementById('parsed') === null"
        )
        assert r.value is True
        sync_dom_mutations(runtime.ctx, runtime.session)
        assert _count_tag(doc, "h1") == 1

    def test_scratch_create_element_still_usable_in_real_dom(self, rt):
        runtime, doc = rt
        # Nodes created via the scratch document's createElement must still
        # be attachable to the live tree (jQuery buildFragment pattern).
        runtime.eval_safe(
            "var d = document.implementation.createHTMLDocument('');"
            "var el = d.createElement('section');"
            "el.setAttribute('id', 'from-scratch');"
            "el.textContent = 'moved into the live document body content';"
            "document.body.appendChild(el);"
        )
        sync_dom_mutations(runtime.ctx, runtime.session)
        assert _count_tag(doc, "section") == 1


# ─────────────────────────────────────────────────────────────────────────────
# document.write append semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentWrite:
    def test_write_appends_instead_of_replacing(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.write('<p id=\"written\">W</p>');")
        sync_dom_mutations(runtime.ctx, runtime.session)
        # Existing content retained AND new content appended
        assert _count_tag(doc, "h1") == 1
        assert _count_tag(doc, "p") == 3

    def test_write_empty_is_noop(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.write('');")
        sync_dom_mutations(runtime.ctx, runtime.session)
        assert _count_tag(doc, "p") == 2


# ─────────────────────────────────────────────────────────────────────────────
# Timer cascade budget
# ─────────────────────────────────────────────────────────────────────────────

class TestTimerBudget:
    @pytest.mark.asyncio
    async def test_self_rescheduling_timer_chain_is_bounded(self, rt):
        runtime, _ = rt
        runtime.eval_safe(
            "var _chain = 0;"
            "function tick() { _chain++; setTimeout(tick, 0); }"
            "setTimeout(tick, 0);"
        )
        start = time.monotonic()
        await runtime.drain_microtasks()
        elapsed = time.monotonic() - start
        # Without the batch cap + budget this cascades until the eval
        # timeout (or forever with the old engine).
        assert elapsed < 3.0
        assert runtime.eval_safe("_chain").value >= 1

    @pytest.mark.asyncio
    async def test_many_ready_timers_fire_across_drains(self, rt):
        runtime, _ = rt
        runtime.eval_safe(
            "var _fired = 0;"
            "for (var i = 0; i < 120; i++) {"
            "  setTimeout(function(){ _fired++; }, 0);"
            "}"
        )
        # Batch cap is 50 per drain — three drains must fire them all.
        for _ in range(3):
            await runtime.drain_microtasks()
        assert runtime.eval_safe("_fired").value == 120


# ─────────────────────────────────────────────────────────────────────────────
# Catastrophic wipe guard
# ─────────────────────────────────────────────────────────────────────────────

class TestWipeGuard:
    def _big_body(self, n: int = 250) -> Element:
        body = Element(node_id="body", tag="body")
        for i in range(n):
            p = Element(node_id=f"p{i}", tag="p")
            p.append_child(TextNode(node_id=f"t{i}", data=f"text {i}"))
            body.append_child(p)
        return body

    def test_large_body_tiny_replacement_is_refused(self):
        assert _is_catastrophic_wipe(self._big_body(), "<form></form>") is True

    def test_large_replacement_is_allowed(self):
        assert _is_catastrophic_wipe(self._big_body(), "x" * 2000) is False

    def test_small_body_replacement_is_allowed(self):
        body = Element(node_id="body", tag="body")
        assert _is_catastrophic_wipe(body, "<div>app</div>") is False

    def test_non_structural_node_is_never_guarded(self):
        root = Element(node_id="root", tag="div")
        for i in range(300):
            root.append_child(Element(node_id=f"d{i}", tag="span"))
        assert _is_catastrophic_wipe(root, "") is False


# ─────────────────────────────────────────────────────────────────────────────
# Per-eval V8 timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestEvalTimeout:
    def test_runaway_script_is_interrupted(self, rt):
        runtime, _ = rt
        start = time.monotonic()
        r = runtime.eval_safe("while (true) {}", timeout_ms=400)
        elapsed = time.monotonic() - start
        assert r.ok is False
        assert elapsed < 5.0

    def test_runtime_survives_timeout(self, rt):
        runtime, _ = rt
        runtime.eval_safe("while (true) {}", timeout_ms=300)
        assert runtime.eval_safe("1 + 1").value == 2
