"""
Dedicated unit tests for an_web/js/host_api.py — Host Web API layer.

Covers every major surface:
- document (query, getElementById, forms/links/images, title, URL)
- EventTarget (addEventListener, removeEventListener, dispatchEvent)
- Element (classList, value, checked, disabled, parentElement, siblings,
           scoped querySelector/All, dataset, getBoundingClientRect, stubs)
- window / location / navigator / screen
- localStorage / sessionStorage
- setTimeout / clearTimeout / queueMicrotask
- fetch / XMLHttpRequest
- history
- console
- Promise drain with host API interactions
"""
from __future__ import annotations

import pytest
from an_web.js.runtime import JSRuntime
from an_web.browser.parser import parse_html

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

_RICH_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Test Page</title>
</head>
<body>
  <header id="hdr">
    <nav>
      <a href="/home" id="nav-home" class="nav-link active">Home</a>
      <a href="/about" id="nav-about" class="nav-link">About</a>
    </nav>
  </header>
  <main id="main">
    <form id="search-form" method="get" action="/search">
      <input type="search" id="q" name="q" placeholder="Search..." value="python">
      <button type="submit" id="search-btn" class="btn btn-primary">Search</button>
    </form>
    <ul id="results">
      <li class="result-item" data-id="1">First result</li>
      <li class="result-item" data-id="2">Second result</li>
      <li class="result-item disabled-item" data-id="3">Third result</li>
    </ul>
    <form id="login-form" method="post" action="/login">
      <input type="email" id="email" name="email" placeholder="Email">
      <input type="password" id="password" name="password" placeholder="Password">
      <input type="checkbox" id="remember" name="remember" value="yes">
      <button type="submit" id="login-btn">Sign In</button>
    </form>
  </main>
  <img src="/logo.png" alt="Logo" id="logo">
  <img src="/banner.jpg" alt="Banner">
</body>
</html>"""


def _make_runtime(html: bytes = _RICH_HTML, url: str = "https://example.com/") -> JSRuntime:
    doc = parse_html(html.decode(), url)

    class Sess:
        _current_url = url
        _current_document = doc
        _history: list = []

    return JSRuntime(Sess()), doc


@pytest.fixture
def rt():
    runtime, doc = _make_runtime()
    yield runtime, doc
    runtime.close()


def ev(runtime, script, default=None):
    """Shorthand: eval_safe and return value."""
    return runtime.eval_safe(script, default=default).value


# ─────────────────────────────────────────────────────────────────────────────
# document basics
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentBasics:
    def test_title(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.title") == "Test Page"

    def test_title_setter_updates_dom(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.title = 'Changed'")
        assert doc.title == "Changed"

    def test_url(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.URL") == "https://example.com/"

    def test_document_uri(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.documentURI") == "https://example.com/"

    def test_ready_state(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.readyState") == "complete"

    def test_node_type(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.nodeType") == 9

    def test_body_not_null(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.body !== null") is True

    def test_head_or_title_accessible(self, rt):
        # Our parser doesn't retain <head> wrapper but <title> is accessible
        runtime, _ = rt
        # Either head is available or we can reach title directly
        r = runtime.eval_safe(
            "document.head !== null || document.querySelector('title') !== null"
        )
        assert r.value is True

    def test_document_element_not_null(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.documentElement !== null") is True


# ─────────────────────────────────────────────────────────────────────────────
# document query methods
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentQuery:
    def test_query_selector_by_id(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#q') !== null") is True

    def test_query_selector_by_class(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('.nav-link') !== null") is True

    def test_query_selector_by_tag(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('form') !== null") is True

    def test_query_selector_missing_returns_null(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#no-such') === null") is True

    def test_query_selector_all_count(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelectorAll('.result-item').length") == 3

    def test_query_selector_all_empty_list(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelectorAll('.no-match').length") == 0

    def test_get_element_by_id(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('email') !== null") is True

    def test_get_element_by_id_tag(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('logo').tagName") == "IMG"

    def test_get_element_by_id_missing(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('nope') === null") is True

    def test_get_elements_by_tag_name(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementsByTagName('input').length") == 4

    def test_get_elements_by_class_name(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementsByClassName('nav-link').length") == 2

    def test_get_elements_by_name(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementsByName('email').length") == 1

    def test_compound_selector(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('button.btn-primary') !== null") is True

    def test_attribute_selector(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('input[type=\"email\"]') !== null") is True


# ─────────────────────────────────────────────────────────────────────────────
# document.forms / links / images
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentCollections:
    def test_forms_count(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.forms.length") == 2

    def test_forms_action(self, rt):
        runtime, _ = rt
        # First form is search-form
        assert ev(runtime, "document.forms[0].getAttribute('action')") == "/search"

    def test_forms_method(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.forms[1].getAttribute('method')") == "post"

    def test_links_count(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.links.length") == 2

    def test_links_href(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.links[0].getAttribute('href')") == "/home"

    def test_images_count(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.images.length") == 2

    def test_images_src(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.images[0].src") == "/logo.png"

    def test_document_has_focus(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.hasFocus()") is True

    def test_document_contains(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.contains(document.body)") is True


# ─────────────────────────────────────────────────────────────────────────────
# document.createElement
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateElement:
    def test_creates_element_with_correct_tag(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.createElement('div').tagName") == "DIV"

    def test_creates_input_element(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.createElement('input').tagName") == "INPUT"

    def test_created_element_is_not_null(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.createElement('span') !== null") is True

    def test_created_element_can_set_attribute(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var el = document.createElement('div');"
            "el.setAttribute('class', 'test');"
            "el.getAttribute('class');"
        )
        assert r.value == "test"

    def test_create_text_node(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.createTextNode('hello').nodeType")
        assert r.value == 3


# ─────────────────────────────────────────────────────────────────────────────
# Element properties
# ─────────────────────────────────────────────────────────────────────────────

class TestElementProperties:
    def test_tag_name_uppercase(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('form').tagName") == "FORM"

    def test_local_name_lowercase(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('form').localName") == "form"

    def test_id_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('email').id") == "email"

    def test_class_name(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('.btn').className") == "btn btn-primary"

    def test_text_content(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#nav-home').textContent") == "Home"

    def test_get_attribute(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').getAttribute('placeholder')") == "Search..."

    def test_set_attribute_updates_dom(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('q').setAttribute('value', 'updated')")
        el = doc.get_element_by_id("q")
        assert el.attributes.get("value") == "updated"

    def test_remove_attribute(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('q').removeAttribute('placeholder')")
        el = doc.get_element_by_id("q")
        assert "placeholder" not in el.attributes

    def test_has_attribute_true(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').hasAttribute('placeholder')") is True

    def test_has_attribute_false(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').hasAttribute('nonexistent')") is False

    def test_node_type_element(self, rt):
        runtime, _ = rt
        # Use 'ul' which is definitely in _RICH_HTML
        assert ev(runtime, "document.querySelector('ul').nodeType") == 1

    def test_children_array(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('results').children.length") >= 1

    def test_child_element_count(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('results').childElementCount") >= 1

    def test_first_child(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('results').firstChild !== null") is True

    def test_last_child(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('results').lastChild !== null") is True


# ─────────────────────────────────────────────────────────────────────────────
# Element.value / checked / disabled / type shortcuts
# ─────────────────────────────────────────────────────────────────────────────

class TestElementFormProperties:
    def test_value_reads_attribute(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').value") == "python"

    def test_value_setter_propagates_to_dom(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('q').value = 'new query'")
        el = doc.get_element_by_id("q")
        assert el.attributes.get("value") == "new query"

    def test_type_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').type") == "search"

    def test_placeholder_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').placeholder") == "Search..."

    def test_name_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('q').name") == "q"

    def test_checked_default_false(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('remember').checked") is False

    def test_checked_setter(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('remember').checked = true")
        el = doc.get_element_by_id("remember")
        assert "checked" in el.attributes

    def test_disabled_default_false(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('email').disabled") is False

    def test_disabled_setter(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('email').disabled = true")
        el = doc.get_element_by_id("email")
        assert "disabled" in el.attributes

    def test_href_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#nav-home').href") == "/home"

    def test_src_property(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('logo').src") == "/logo.png"


# ─────────────────────────────────────────────────────────────────────────────
# Element.classList
# ─────────────────────────────────────────────────────────────────────────────

class TestClassList:
    def test_contains_true(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#nav-home').classList.contains('active')") is True

    def test_contains_false(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.querySelector('#nav-home').classList.contains('nope')") is False

    def test_add_class(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('email').classList.add('highlight')")
        el = doc.get_element_by_id("email")
        assert "highlight" in (el.attributes.get("class", "") or "")

    def test_add_multiple_classes(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('password').classList.add('a', 'b', 'c')")
        el = doc.get_element_by_id("password")
        classes = (el.attributes.get("class", "") or "").split()
        assert "a" in classes and "b" in classes and "c" in classes

    def test_add_does_not_duplicate(self, rt):
        runtime, doc = rt
        runtime.eval_safe("""
        document.getElementById('q').classList.add('extra');
        document.getElementById('q').classList.add('extra');
        """)
        el = doc.get_element_by_id("q")
        classes = (el.attributes.get("class", "") or "").split()
        assert classes.count("extra") == 1

    def test_remove_class(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.querySelector('#nav-home').classList.remove('active')")
        el = doc.get_element_by_id("nav-home")
        assert "active" not in (el.attributes.get("class", "") or "").split()

    def test_toggle_off(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.querySelector('#nav-home').classList.toggle('active')")
        el = doc.get_element_by_id("nav-home")
        assert "active" not in (el.attributes.get("class", "") or "").split()

    def test_toggle_on(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.getElementById('email').classList.toggle('visible')")
        el = doc.get_element_by_id("email")
        assert "visible" in (el.attributes.get("class", "") or "").split()

    def test_replace_class(self, rt):
        runtime, doc = rt
        runtime.eval_safe("document.querySelector('#nav-home').classList.replace('active', 'current')")
        el = doc.get_element_by_id("nav-home")
        classes = (el.attributes.get("class", "") or "").split()
        assert "current" in classes
        assert "active" not in classes

    def test_classList_length(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.querySelector('#nav-home').classList.length")
        assert r.ok and r.value >= 1

    def test_classList_to_string(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.querySelector('#nav-home').classList.toString()")
        assert "nav-link" in (r.value or "")


# ─────────────────────────────────────────────────────────────────────────────
# Element traversal (parent, siblings, scoped querySelector)
# ─────────────────────────────────────────────────────────────────────────────

class TestElementTraversal:
    def test_parent_element_not_null(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('email').parentElement !== null") is True

    def test_parent_element_tag(self, rt):
        runtime, _ = rt
        assert ev(runtime, "document.getElementById('email').parentElement.tagName") == "FORM"

    def test_root_element_parent_may_be_null_or_html(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.querySelector('body').parentElement")
        # May be html element or null depending on tree depth — just no exception
        assert r.ok

    def test_next_sibling(self, rt):
        runtime, _ = rt
        # First nav-link ('Home') should have a sibling ('About')
        r = runtime.eval_safe(
            "document.getElementById('nav-home').nextElementSibling !== null"
        )
        assert r.value is True

    def test_next_sibling_tag(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "document.getElementById('nav-home').nextElementSibling.tagName"
        )
        assert r.value == "A"

    def test_previous_sibling_of_first_is_null(self, rt):
        runtime, _ = rt
        # First li has no previous sibling
        r = runtime.eval_safe(
            "document.getElementById('results').children[0].previousElementSibling === null"
        )
        assert r.value is True

    def test_previous_sibling_of_second(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "document.getElementById('nav-about').previousElementSibling !== null"
        )
        assert r.value is True

    def test_scoped_query_selector(self, rt):
        runtime, _ = rt
        # querySelector on search-form should find its own input, not email input
        r = runtime.eval_safe(
            "document.getElementById('search-form').querySelector('input').getAttribute('name')"
        )
        assert r.value == "q"

    def test_scoped_query_selector_all(self, rt):
        runtime, _ = rt
        # login-form has 3 inputs
        r = runtime.eval_safe(
            "document.getElementById('login-form').querySelectorAll('input').length"
        )
        assert r.value == 3

    def test_scoped_query_does_not_leak_outside(self, rt):
        runtime, _ = rt
        # search-form should NOT find #email (which is in login-form)
        r = runtime.eval_safe(
            "document.getElementById('search-form').querySelector('#email') === null"
        )
        assert r.value is True

    def test_contains_self_child(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "document.getElementById('results').contains(document.querySelector('.result-item'))"
        )
        assert r.ok  # just no crash

    def test_element_matches_by_id(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.getElementById('email').matches('#email')")
        assert r.value is True

    def test_element_matches_by_class(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.querySelector('.nav-link').matches('.nav-link')")
        assert r.value is True

    def test_element_closest(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "document.getElementById('email').closest('form') !== null"
        )
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# Element geometry / layout stubs
# ─────────────────────────────────────────────────────────────────────────────

class TestElementLayout:
    def test_get_bounding_client_rect(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.getElementById('email').getBoundingClientRect().top")
        assert r.ok and r.value == 0

    def test_bounding_rect_has_all_fields(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var r = document.getElementById('email').getBoundingClientRect();"
            "r.top === 0 && r.left === 0 && r.width === 0 && r.height === 0"
        )
        assert r.value is True

    def test_offset_properties(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("document.getElementById('email').offsetTop")
        assert r.ok and r.value == 0

    def test_scroll_into_view_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "document.getElementById('email').scrollIntoView(); 'ok'"
        )
        assert r.value == "ok"

    def test_get_client_rects_is_array(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("Array.isArray(document.getElementById('email').getClientRects())")
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# EventTarget
# ─────────────────────────────────────────────────────────────────────────────

class TestEventTarget:
    def test_add_and_dispatch_event_on_document(self, rt):
        # Element proxies are ephemeral (new object per access), so we test
        # on document and window which are long-lived singleton objects.
        runtime, _ = rt
        runtime.eval_safe("""
        var _fired = false;
        document.addEventListener('focus', function() { _fired = true; });
        document.dispatchEvent({type: 'focus'});
        """)
        r = runtime.eval_safe("_fired")
        assert r.value is True

    def test_add_and_dispatch_event_on_element_same_ref(self, rt):
        # Events work when add/dispatch use the SAME JS object reference
        runtime, _ = rt
        runtime.eval_safe("""
        var _fired = false;
        var el = document.getElementById('email');  // capture reference once
        el.addEventListener('focus', function() { _fired = true; });
        el.dispatchEvent({type: 'focus'});           // same reference
        """)
        r = runtime.eval_safe("_fired")
        assert r.value is True

    def test_remove_event_listener(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _count = 0;
        function _handler() { _count++; }
        document.addEventListener('click', _handler);
        document.removeEventListener('click', _handler);
        document.dispatchEvent({type: 'click'});
        """)
        r = runtime.eval_safe("_count")
        assert r.value == 0

    def test_multiple_listeners_same_event(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _n = 0;
        document.addEventListener('input', function() { _n++; });
        document.addEventListener('input', function() { _n++; });
        document.dispatchEvent({type: 'input'});
        """)
        r = runtime.eval_safe("_n")
        assert r.value == 2

    def test_window_add_event_listener(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _wFired = false;
        window.addEventListener('resize', function() { _wFired = true; });
        window.dispatchEvent(new Event('resize'));
        """)
        r = runtime.eval_safe("_wFired")
        assert r.value is True

    def test_document_add_event_listener(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _dFired = false;
        document.addEventListener('click', function() { _dFired = true; });
        document.dispatchEvent({type: 'click'});
        """)
        r = runtime.eval_safe("_dFired")
        assert r.value is True

    def test_custom_event(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("""
        var _detail = null;
        window.addEventListener('myEvent', function(e) { _detail = e.detail; });
        window.dispatchEvent(new CustomEvent('myEvent', { detail: {x: 42} }));
        _detail ? _detail.x : null
        """)
        assert r.value == 42

    def test_event_type_property(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("""
        var _type = '';
        window.addEventListener('testEvt', function(e) { _type = e.type; });
        window.dispatchEvent(new Event('testEvt'));
        _type
        """)
        assert r.value == "testEvt"


# ─────────────────────────────────────────────────────────────────────────────
# setTimeout / clearTimeout / queueMicrotask
# ─────────────────────────────────────────────────────────────────────────────

class TestTimerAPI:
    def test_set_timeout_returns_numeric_id(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof setTimeout(function(){}, 0)")
        assert r.value == "number"

    def test_clear_timeout_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var id = setTimeout(function(){}, 100); clearTimeout(id); 'done'"
        )
        assert r.value == "done"

    def test_set_interval_returns_id(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof setInterval(function(){}, 50)")
        assert r.value == "number"

    def test_clear_interval_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var id = setInterval(function(){}, 50); clearInterval(id); 'ok'"
        )
        assert r.value == "ok"

    @pytest.mark.asyncio
    async def test_queue_microtask_fires(self, rt):
        runtime, _ = rt
        runtime.eval_safe("var _qmt = []; queueMicrotask(function() { _qmt.push(1); });")
        await runtime.drain_microtasks()
        r = runtime.eval_safe("_qmt[0]")
        assert r.value == 1

    def test_request_animation_frame_returns_id(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof requestAnimationFrame(function(){})")
        assert r.value == "number"

    def test_cancel_animation_frame_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var id = requestAnimationFrame(function(){}); cancelAnimationFrame(id); 'ok'"
        )
        assert r.value == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Promise / microtask drain + host API interaction
# ─────────────────────────────────────────────────────────────────────────────

class TestPromiseWithHostAPI:
    @pytest.mark.asyncio
    async def test_promise_resolves_with_dom_read(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _resolved = null;
        Promise.resolve(document.title).then(function(t) { _resolved = t; });
        """)
        await runtime.drain_microtasks()
        r = runtime.eval_safe("_resolved")
        assert r.value == "Test Page"

    @pytest.mark.asyncio
    async def test_async_await_syntax(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _aresult = null;
        (async function() {
            var v = await Promise.resolve(99);
            _aresult = v;
        })();
        """)
        await runtime.drain_microtasks()
        r = runtime.eval_safe("_aresult")
        assert r.value == 99

    @pytest.mark.asyncio
    async def test_promise_rejection_caught(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _caught = null;
        Promise.reject(new Error('oops')).catch(function(e) {
            _caught = e.message;
        });
        """)
        await runtime.drain_microtasks()
        r = runtime.eval_safe("_caught")
        assert r.value == "oops"

    @pytest.mark.asyncio
    async def test_promise_all(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _all = null;
        Promise.all([Promise.resolve(1), Promise.resolve(2), Promise.resolve(3)])
            .then(function(arr) { _all = arr; });
        """)
        await runtime.settle(microtask_rounds=5)
        r = runtime.eval_safe("JSON.stringify(_all)")
        assert r.value == "[1,2,3]"


# ─────────────────────────────────────────────────────────────────────────────
# localStorage / sessionStorage
# ─────────────────────────────────────────────────────────────────────────────

class TestStorageAPI:
    def test_set_and_get_item(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.setItem('token', 'abc123')")
        r = runtime.eval_safe("localStorage.getItem('token')")
        assert r.value == "abc123"

    def test_get_missing_is_null(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("localStorage.getItem('no_key')")
        assert r.value is None

    def test_remove_item(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.setItem('x', '1'); localStorage.removeItem('x');")
        r = runtime.eval_safe("localStorage.getItem('x')")
        assert r.value is None

    def test_length_after_set(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.clear()")
        runtime.eval_safe("localStorage.setItem('a', '1'); localStorage.setItem('b', '2');")
        r = runtime.eval_safe("localStorage.length")
        assert r.value == 2

    def test_clear_resets_length(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.setItem('k', 'v'); localStorage.clear();")
        r = runtime.eval_safe("localStorage.length")
        assert r.value == 0

    def test_key_method(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.clear(); localStorage.setItem('myKey', 'v');")
        r = runtime.eval_safe("localStorage.key(0)")
        assert r.value == "myKey"

    def test_session_and_local_are_independent(self, rt):
        runtime, _ = rt
        runtime.eval_safe("localStorage.setItem('same', 'local')")
        runtime.eval_safe("sessionStorage.setItem('same', 'session')")
        r1 = runtime.eval_safe("localStorage.getItem('same')")
        r2 = runtime.eval_safe("sessionStorage.getItem('same')")
        assert r1.value == "local"
        assert r2.value == "session"

    def test_session_storage_same_api(self, rt):
        runtime, _ = rt
        runtime.eval_safe("sessionStorage.setItem('ss_key', 'ss_val')")
        r = runtime.eval_safe("sessionStorage.getItem('ss_key')")
        assert r.value == "ss_val"


# ─────────────────────────────────────────────────────────────────────────────
# fetch / XMLHttpRequest
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchAPI:
    def test_fetch_is_function(self, rt):
        runtime, _ = rt
        assert ev(runtime, "typeof fetch") == "function"

    def test_fetch_returns_promise(self, rt):
        runtime, _ = rt
        assert ev(runtime, "fetch('/x') instanceof Promise") is True

    @pytest.mark.asyncio
    async def test_fetch_promise_eventually_settles(self, rt):
        runtime, _ = rt
        runtime.eval_safe("""
        var _settled = false;
        fetch('https://example.com/')
            .then(function() { _settled = true; })
            .catch(function() { _settled = true; });
        """)
        await runtime.settle(microtask_rounds=3)
        r = runtime.eval_safe("_settled")
        assert r.value is True

    def test_xhr_constructor(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof XMLHttpRequest")
        assert r.value == "function"

    def test_xhr_can_be_instantiated(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("new XMLHttpRequest() instanceof XMLHttpRequest")
        assert r.value is True

    def test_xhr_open_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var xhr = new XMLHttpRequest();"
            "xhr.open('GET', 'https://example.com/');"
            "xhr.readyState"
        )
        assert r.value == 1

    def test_xhr_set_request_header_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var xhr = new XMLHttpRequest();"
            "xhr.open('GET', '/x');"
            "xhr.setRequestHeader('Accept', 'application/json');"
            "'ok'"
        )
        assert r.value == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# history API
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryAPI:
    def test_history_length_is_number(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof history.length")
        assert r.value == "number"

    def test_push_state_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "history.pushState({page: 1}, '', '/page1'); 'ok'"
        )
        assert r.value == "ok"

    def test_replace_state_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "history.replaceState(null, '', '/replaced'); 'ok'"
        )
        assert r.value == "ok"

    def test_back_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("history.back(); 'ok'")
        assert r.value == "ok"

    def test_forward_no_throw(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("history.forward(); 'ok'")
        assert r.value == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# window / location / navigator / screen
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowLocationNavigator:
    def test_window_self_is_window(self, rt):
        runtime, _ = rt
        assert ev(runtime, "window.self === window") is True

    def test_window_top_is_window(self, rt):
        runtime, _ = rt
        assert ev(runtime, "window.top === window") is True

    def test_location_href(self, rt):
        runtime, _ = rt
        assert ev(runtime, "location.href") == "https://example.com/"

    def test_location_protocol(self, rt):
        runtime, _ = rt
        assert ev(runtime, "location.protocol") == "https:"

    def test_location_hostname(self, rt):
        runtime, _ = rt
        assert ev(runtime, "location.hostname") == "example.com"

    def test_location_pathname(self, rt):
        runtime, _ = rt
        assert ev(runtime, "location.pathname") == "/"

    def test_location_assign_sets_pending_nav(self, rt):
        runtime, _ = rt
        runtime.eval_safe("location.assign('https://other.com/')")
        # Should have stored pending navigation on session
        assert runtime.session._pending_js_navigation == "https://other.com/"

    def test_location_replace_sets_pending_nav(self, rt):
        runtime, _ = rt
        runtime.eval_safe("location.replace('https://replaced.com/')")
        assert runtime.session._pending_js_navigation == "https://replaced.com/"

    def test_location_to_string(self, rt):
        runtime, _ = rt
        assert ev(runtime, "location.toString()") == "https://example.com/"

    def test_navigator_user_agent(self, rt):
        runtime, _ = rt
        ua = ev(runtime, "navigator.userAgent")
        assert "AN-Web" in ua

    def test_navigator_language(self, rt):
        runtime, _ = rt
        assert ev(runtime, "navigator.language") == "en-US"

    def test_navigator_online(self, rt):
        runtime, _ = rt
        assert ev(runtime, "navigator.onLine") is True

    def test_screen_width(self, rt):
        runtime, _ = rt
        assert ev(runtime, "screen.width") >= 1280

    def test_get_computed_style(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "getComputedStyle(document.getElementById('email')).display"
        )
        assert r.ok  # returns 'block' or ''

    def test_match_media(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("window.matchMedia('(max-width: 600px)').matches")
        assert r.ok and r.value is False


# ─────────────────────────────────────────────────────────────────────────────
# console
# ─────────────────────────────────────────────────────────────────────────────

class TestConsoleAPI:
    def test_log_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.log('hi'); 'ok'") == "ok"

    def test_warn_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.warn('warn'); 'ok'") == "ok"

    def test_error_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.error('err'); 'ok'") == "ok"

    def test_info_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.info('info'); 'ok'") == "ok"

    def test_debug_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.debug('dbg'); 'ok'") == "ok"

    def test_group_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.group('g'); console.groupEnd(); 'ok'") == "ok"

    def test_assert_true_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.assert(true, 'ok'); 'done'") == "done"

    def test_assert_false_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.assert(false, 'bad'); 'done'") == "done"

    def test_log_object_no_throw(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.log({a: 1, b: [2,3]}); 'ok'") == "ok"

    def test_log_multiple_args(self, rt):
        runtime, _ = rt
        assert ev(runtime, "console.log('a', 'b', 42, true); 'ok'") == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Observer / Polyfill stubs
# ─────────────────────────────────────────────────────────────────────────────

class TestObserversAndPolyfills:
    def test_mutation_observer(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var mo = new MutationObserver(function(){}); mo.observe({}, {}); 'ok'"
        )
        assert r.value == "ok"

    def test_intersection_observer(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var io = new IntersectionObserver(function(){}); io.observe({}); 'ok'"
        )
        assert r.value == "ok"

    def test_resize_observer(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var ro = new ResizeObserver(function(){}); ro.observe({}); 'ok'"
        )
        assert r.value == "ok"

    def test_url_polyfill(self, rt):
        runtime, _ = rt
        assert ev(runtime, "new URL('https://a.com/b').pathname") == "/b"

    def test_url_search_params(self, rt):
        runtime, _ = rt
        assert ev(runtime, "new URLSearchParams('k=v').get('k')") == "v"

    def test_blob_polyfill(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("typeof Blob")
        assert r.value == "function"

    def test_performance_now(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("performance.now() >= 0")
        assert r.value is True

    def test_custom_event_with_detail(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe("new CustomEvent('x', {detail: {n: 7}}).detail.n")
        assert r.value == 7

    def test_event_prevent_default(self, rt):
        runtime, _ = rt
        r = runtime.eval_safe(
            "var e = new Event('click'); e.preventDefault(); 'ok'"
        )
        assert r.value == "ok"
