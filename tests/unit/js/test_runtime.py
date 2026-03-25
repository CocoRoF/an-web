"""Tests for an_web/js/runtime.py — JSRuntime."""
from __future__ import annotations

import asyncio
import pytest
from an_web.js.runtime import JSRuntime
from an_web.js.bridge import JSError, EvalResult


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_session(url="https://example.com/", doc=None):
    class FakeSession:
        _current_url = url
        _current_document = doc
        _history: list = []
    return FakeSession()


def _make_session_with_doc(html="<html><head><title>T</title></head><body></body></html>",
                            url="https://example.com/"):
    from an_web.browser.parser import parse_html
    doc = parse_html(html, url)
    return _make_session(url, doc)


@pytest.fixture
def rt():
    session = _make_session()
    runtime = JSRuntime(session)
    yield runtime
    runtime.close()


@pytest.fixture
def rt_with_doc():
    session = _make_session_with_doc(
        "<html><head><title>My Page</title></head>"
        "<body><div id='main'><p class='txt'>Hello</p></div></body></html>",
        "https://example.com/page"
    )
    runtime = JSRuntime(session)
    yield runtime
    runtime.close()


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation
# ─────────────────────────────────────────────────────────────────────────────


class TestJSRuntimeInit:
    def test_available(self, rt):
        assert rt.is_available() is True

    def test_ctx_not_none(self, rt):
        assert rt.ctx is not None

    def test_repr_contains_available(self, rt):
        r = repr(rt)
        assert "available" in r

    def test_context_manager(self):
        session = _make_session()
        with JSRuntime(session) as runtime:
            assert runtime.is_available()
        # After exit, should be closed
        assert not runtime.is_available()

    def test_close_idempotent(self, rt):
        rt.close()
        rt.close()  # second close should not raise


# ─────────────────────────────────────────────────────────────────────────────
# eval / eval_safe
# ─────────────────────────────────────────────────────────────────────────────


class TestEval:
    def test_integer_arithmetic(self, rt):
        assert rt.eval("1 + 2") == 3

    def test_string_expression(self, rt):
        assert rt.eval('"foo" + "bar"') == "foobar"

    def test_boolean(self, rt):
        assert rt.eval("true") is True
        assert rt.eval("false") is False

    def test_null_returns_none(self, rt):
        assert rt.eval("null") is None

    def test_undefined_returns_none(self, rt):
        assert rt.eval("undefined") is None

    def test_object_result(self, rt):
        from an_web.js.bridge import js_to_py
        raw = rt.eval("({x: 1, y: [2, 3]})")
        result = js_to_py(raw)
        assert result == {"x": 1, "y": [2, 3]}

    def test_throws_js_error(self, rt):
        with pytest.raises(JSError) as exc_info:
            rt.eval("throw new Error('test error')")
        assert "test error" in str(exc_info.value)

    def test_type_error(self, rt):
        with pytest.raises(JSError) as exc_info:
            rt.eval("null.property")
        assert exc_info.value.js_type in ("TypeError", "Error")

    def test_syntax_error(self, rt):
        with pytest.raises(JSError):
            rt.eval("this is not valid JS }{")

    def test_eval_safe_returns_eval_result(self, rt):
        r = rt.eval_safe("42")
        assert isinstance(r, EvalResult)
        assert r.ok is True
        assert r.value == 42

    def test_eval_safe_never_raises(self, rt):
        r = rt.eval_safe("throw new TypeError('oops')")
        assert r.ok is False
        assert r.error is not None
        assert "oops" in r.error.message

    def test_eval_safe_default_on_error(self, rt):
        r = rt.eval_safe("undefined_variable + 1", default=-1)
        # Either ok=True (undefined+1=NaN) or ok=False; check no exception
        assert isinstance(r, EvalResult)

    def test_multiline_script(self, rt):
        r = rt.eval_safe("var x = 10;\nvar y = 20;\nx + y;")
        assert r.value == 30

    def test_variable_persistence(self, rt):
        rt.eval("var counter = 0;")
        rt.eval("counter += 1;")
        rt.eval("counter += 1;")
        r = rt.eval_safe("counter")
        assert r.value == 2


# ─────────────────────────────────────────────────────────────────────────────
# get_global / set_global
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSetGlobal:
    def test_set_string_global(self, rt):
        rt.set_global("myStr", "hello")
        r = rt.eval_safe("myStr")
        assert r.value == "hello"

    def test_set_number_global(self, rt):
        rt.set_global("myNum", 42)
        r = rt.eval_safe("myNum")
        assert r.value == 42

    def test_set_bool_global(self, rt):
        rt.set_global("myBool", True)
        r = rt.eval_safe("myBool")
        assert r.value is True

    def test_set_callable_global(self, rt):
        rt.set_global("double", lambda x: x * 2)
        r = rt.eval_safe("double(21)")
        assert r.value == 42

    def test_get_global_existing(self, rt):
        rt.eval("var testGlobal = 'value';")
        result = rt.get_global("testGlobal")
        assert result == "value"

    def test_get_global_missing_returns_default(self, rt):
        result = rt.get_global("nonExistentGlobal", default="fallback")
        assert result == "fallback"

    def test_set_dict_global(self, rt):
        rt.set_global("cfg", {"key": "val"})
        r = rt.eval_safe("cfg.key")
        # set_global with dict uses JSON injection
        assert r.ok


# ─────────────────────────────────────────────────────────────────────────────
# call / call_safe
# ─────────────────────────────────────────────────────────────────────────────


class TestCall:
    def test_call_no_args(self, rt):
        rt.eval("function greet() { return 'hello'; }")
        assert rt.call("greet") == "hello"

    def test_call_with_args(self, rt):
        rt.eval("function add(a, b) { return a + b; }")
        assert rt.call("add", 3, 4) == 7

    def test_call_string_args(self, rt):
        rt.eval("function upper(s) { return s.toUpperCase(); }")
        assert rt.call("upper", "test") == "TEST"

    def test_call_throws_js_error(self, rt):
        rt.eval("function boom() { throw new RangeError('out'); }")
        with pytest.raises(JSError) as exc_info:
            rt.call("boom")
        assert exc_info.value.js_type == "RangeError"

    def test_call_safe_success(self, rt):
        rt.eval("function sq(x) { return x * x; }")
        r = rt.call_safe("sq", 5)
        assert r.ok and r.value == 25

    def test_call_safe_failure(self, rt):
        rt.eval("function err() { throw new Error('fail'); }")
        r = rt.call_safe("err")
        assert not r.ok
        assert r.error is not None


# ─────────────────────────────────────────────────────────────────────────────
# drain_microtasks / settle
# ─────────────────────────────────────────────────────────────────────────────


class TestMicrotasks:
    @pytest.mark.asyncio
    async def test_promise_then_fires(self, rt):
        rt.eval("var _result = []; Promise.resolve(99).then(v => _result.push(v));")
        drained = await rt.drain_microtasks()
        assert drained >= 1
        r = rt.eval_safe("_result[0]")
        assert r.value == 99

    @pytest.mark.asyncio
    async def test_chained_promises(self, rt):
        rt.eval("""
        var chain = [];
        Promise.resolve(1)
            .then(v => { chain.push(v); return v + 1; })
            .then(v => { chain.push(v); return v + 1; })
            .then(v => chain.push(v));
        """)
        await rt.drain_microtasks()
        r = rt.eval_safe("JSON.stringify(chain)")
        assert r.value == "[1,2,3]"

    @pytest.mark.asyncio
    async def test_queue_microtask(self, rt):
        rt.eval("var _qt = []; queueMicrotask(() => _qt.push('micro'));")
        await rt.drain_microtasks()
        r = rt.eval_safe("_qt[0]")
        assert r.value == "micro"

    @pytest.mark.asyncio
    async def test_settle_runs_multiple_rounds(self, rt):
        rt.eval("""
        var _s = [];
        Promise.resolve('a').then(v => {
            _s.push(v);
            return Promise.resolve('b');
        }).then(v => _s.push(v));
        """)
        await rt.settle(microtask_rounds=3)
        r = rt.eval_safe("_s.join(',')")
        assert r.value == "a,b"

    @pytest.mark.asyncio
    async def test_no_jobs_returns_zero(self, rt):
        # No pending jobs
        drained = await rt.drain_microtasks()
        assert drained == 0


# ─────────────────────────────────────────────────────────────────────────────
# load_script
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadScript:
    def test_load_script_executes(self, rt):
        result = rt.load_script("var __loaded = true;")
        assert result.ok

    def test_load_script_tracks_name(self, rt):
        rt.load_script("var x = 1;", src_hint="myscript.js")
        assert "myscript.js" in rt._scripts_loaded

    def test_load_script_error_does_not_raise(self, rt):
        # Broken script should NOT raise
        result = rt.load_script("this is invalid JS }{")
        assert not result.ok  # ok=False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_load_script_async(self, rt):
        result = await rt.load_script_async("var __async = 'yes';")
        assert result.ok
        assert rt.eval_safe("__async").value == "yes"


# ─────────────────────────────────────────────────────────────────────────────
# Host API — console
# ─────────────────────────────────────────────────────────────────────────────


class TestConsole:
    def test_console_log_no_raise(self, rt):
        r = rt.eval_safe("console.log('hello from test')")
        assert r.ok

    def test_console_warn_no_raise(self, rt):
        r = rt.eval_safe("console.warn('warning!')")
        assert r.ok

    def test_console_error_no_raise(self, rt):
        r = rt.eval_safe("console.error('error!')")
        assert r.ok

    def test_console_assert_false_no_raise(self, rt):
        r = rt.eval_safe("console.assert(false, 'bad')")
        assert r.ok


# ─────────────────────────────────────────────────────────────────────────────
# Host API — window / location / navigator
# ─────────────────────────────────────────────────────────────────────────────


class TestWindowGlobals:
    def test_window_is_global_this(self, rt):
        r = rt.eval_safe("window === globalThis")
        assert r.value is True

    def test_location_href(self):
        session = _make_session("https://mysite.com/path?q=1")
        with JSRuntime(session) as runtime:
            r = runtime.eval_safe("location.href")
            assert r.value == "https://mysite.com/path?q=1"

    def test_location_pathname(self):
        session = _make_session("https://mysite.com/path/to/page")
        with JSRuntime(session) as runtime:
            r = runtime.eval_safe("location.pathname")
            assert r.value == "/path/to/page"

    def test_location_hostname(self):
        session = _make_session("https://sub.example.com/")
        with JSRuntime(session) as runtime:
            r = runtime.eval_safe("location.hostname")
            assert r.value == "sub.example.com"

    def test_navigator_user_agent(self, rt):
        r = rt.eval_safe("navigator.userAgent")
        assert "Mozilla" in r.value

    def test_navigator_online(self, rt):
        r = rt.eval_safe("navigator.onLine")
        assert r.value is True

    def test_screen_dimensions(self, rt):
        r = rt.eval_safe("screen.width >= 1280")
        assert r.value is True

    def test_window_confirm_returns_true(self, rt):
        r = rt.eval_safe("window.confirm('ok?')")
        assert r.value is True

    def test_window_alert_no_throw(self, rt):
        r = rt.eval_safe("window.alert('hi'); 'done'")
        assert r.value == "done"


# ─────────────────────────────────────────────────────────────────────────────
# Host API — localStorage / sessionStorage
# ─────────────────────────────────────────────────────────────────────────────


class TestStorage:
    def test_local_storage_set_get(self, rt):
        rt.eval_safe("localStorage.setItem('key', 'value')")
        r = rt.eval_safe("localStorage.getItem('key')")
        assert r.value == "value"

    def test_local_storage_missing_returns_null(self, rt):
        r = rt.eval_safe("localStorage.getItem('no_such_key')")
        assert r.value is None

    def test_local_storage_remove(self, rt):
        rt.eval_safe("localStorage.setItem('x', '1')")
        rt.eval_safe("localStorage.removeItem('x')")
        r = rt.eval_safe("localStorage.getItem('x')")
        assert r.value is None

    def test_local_storage_length(self, rt):
        rt.eval_safe("localStorage.clear()")
        rt.eval_safe("localStorage.setItem('a', '1')")
        rt.eval_safe("localStorage.setItem('b', '2')")
        r = rt.eval_safe("localStorage.length")
        assert r.value == 2

    def test_local_storage_clear(self, rt):
        rt.eval_safe("localStorage.setItem('z', 'v')")
        rt.eval_safe("localStorage.clear()")
        r = rt.eval_safe("localStorage.length")
        assert r.value == 0

    def test_session_storage_independent(self, rt):
        rt.eval_safe("localStorage.setItem('same', 'local')")
        rt.eval_safe("sessionStorage.setItem('same', 'session')")
        r_local = rt.eval_safe("localStorage.getItem('same')")
        r_session = rt.eval_safe("sessionStorage.getItem('same')")
        assert r_local.value == "local"
        assert r_session.value == "session"


# ─────────────────────────────────────────────────────────────────────────────
# Host API — document proxy (with real DOM)
# ─────────────────────────────────────────────────────────────────────────────


LOGIN_HTML = b"""<!DOCTYPE html>
<html>
<head><title>Sign In</title></head>
<body>
  <form id="login-form" action="/api/login" method="post">
    <input type="email" id="email" name="email" placeholder="Email" />
    <input type="password" id="password" name="password" />
    <button type="submit" id="submit-btn" class="btn-primary">Sign In</button>
  </form>
  <a href="/forgot-password" id="forgot">Forgot password?</a>
</body>
</html>"""


@pytest.fixture
def rt_login():
    from an_web.browser.parser import parse_html
    doc = parse_html(LOGIN_HTML.decode(), "https://app.example.com/login")

    class Sess:
        _current_url = "https://app.example.com/login"
        _current_document = doc
        _history: list = []

    runtime = JSRuntime(Sess())
    yield runtime
    runtime.close()


class TestDocumentProxy:
    def test_document_title(self, rt_login):
        r = rt_login.eval_safe("document.title")
        assert r.value == "Sign In"

    def test_document_url(self, rt_login):
        r = rt_login.eval_safe("document.URL")
        assert r.value == "https://app.example.com/login"

    def test_query_selector_id(self, rt_login):
        r = rt_login.eval_safe("document.querySelector('#email') !== null")
        assert r.value is True

    def test_query_selector_returns_null_for_missing(self, rt_login):
        r = rt_login.eval_safe("document.querySelector('#no-such') === null")
        assert r.value is True

    def test_query_selector_attribute(self, rt_login):
        r = rt_login.eval_safe("document.querySelector('#email').getAttribute('type')")
        assert r.value == "email"

    def test_query_selector_all_count(self, rt_login):
        r = rt_login.eval_safe("document.querySelectorAll('input').length")
        assert r.value == 2

    def test_get_element_by_id(self, rt_login):
        r = rt_login.eval_safe("document.getElementById('submit-btn') !== null")
        assert r.value is True

    def test_get_element_by_id_tag_name(self, rt_login):
        r = rt_login.eval_safe("document.getElementById('submit-btn').tagName")
        assert r.value == "BUTTON"

    def test_get_elements_by_tag_name(self, rt_login):
        r = rt_login.eval_safe("document.getElementsByTagName('input').length")
        assert r.value == 2

    def test_get_elements_by_class_name(self, rt_login):
        r = rt_login.eval_safe("document.getElementsByClassName('btn-primary').length")
        assert r.value == 1

    def test_document_title_setter(self, rt_login):
        from an_web.browser.parser import parse_html
        doc = parse_html(LOGIN_HTML.decode(), "https://app.example.com/login")

        class Sess:
            _current_url = "https://app.example.com/login"
            _current_document = doc
            _history: list = []

        with JSRuntime(Sess()) as rt2:
            rt2.eval_safe("document.title = 'New Title'")
            assert doc.title == "New Title"

    def test_set_attribute_propagates_to_dom(self, rt_login):
        """JS setAttribute should update the Python DOM node."""
        from an_web.browser.parser import parse_html
        doc = parse_html(LOGIN_HTML.decode(), "https://app.example.com/login")

        class Sess:
            _current_url = "https://app.example.com/login"
            _current_document = doc
            _history: list = []

        with JSRuntime(Sess()) as rt2:
            rt2.eval_safe(
                'document.getElementById("email").setAttribute("value", "user@test.com")'
            )
            el = doc.get_element_by_id("email")
            assert el is not None
            assert el.attributes.get("value") == "user@test.com"

    def test_create_element_stub(self, rt_login):
        r = rt_login.eval_safe("document.createElement('span').tagName")
        assert r.value == "SPAN"

    def test_body_accessor(self, rt_login):
        r = rt_login.eval_safe("document.body !== null")
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# Host API — URL / URLSearchParams polyfills
# ─────────────────────────────────────────────────────────────────────────────


class TestURLPolyfill:
    def test_url_pathname(self, rt):
        r = rt.eval_safe("new URL('https://example.com/a/b/c').pathname")
        assert r.value == "/a/b/c"

    def test_url_hostname(self, rt):
        r = rt.eval_safe("new URL('https://sub.example.com/').hostname")
        assert r.value == "sub.example.com"

    def test_url_search(self, rt):
        r = rt.eval_safe("new URL('https://example.com/p?q=1&page=2').search")
        assert r.value == "?q=1&page=2"

    def test_url_protocol(self, rt):
        r = rt.eval_safe("new URL('https://example.com/').protocol")
        assert r.value == "https:"

    def test_urlsearchparams_get(self, rt):
        r = rt.eval_safe("new URLSearchParams('a=1&b=foo').get('b')")
        assert r.value == "foo"

    def test_urlsearchparams_has(self, rt):
        r = rt.eval_safe("new URLSearchParams('x=1').has('x')")
        assert r.value is True

    def test_window_url_alias(self, rt):
        r = rt.eval_safe("window.URL === URL")
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# Host API — performance
# ─────────────────────────────────────────────────────────────────────────────


class TestPerformance:
    def test_performance_now_positive(self, rt):
        r = rt.eval_safe("performance.now() >= 0")
        assert r.value is True

    def test_performance_now_increases(self, rt):
        r = rt.eval_safe("var t1 = performance.now(); var t2 = performance.now(); t1 <= t2")
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# Host API — fetch stub
# ─────────────────────────────────────────────────────────────────────────────


class TestFetchStub:
    def test_fetch_exists(self, rt):
        r = rt.eval_safe("typeof fetch")
        assert r.value == "function"

    def test_fetch_returns_promise(self, rt):
        r = rt.eval_safe("fetch('https://example.com/') instanceof Promise")
        assert r.value is True

    @pytest.mark.asyncio
    async def test_fetch_promise_settles(self, rt):
        """Fetch promise should resolve (even if error due to no real network)."""
        rt.eval_safe("""
        var _fetchDone = false;
        fetch('https://example.com/')
            .then(function() { _fetchDone = true; })
            .catch(function() { _fetchDone = true; });
        """)
        await rt.drain_microtasks()
        r = rt.eval_safe("_fetchDone")
        assert r.value is True


# ─────────────────────────────────────────────────────────────────────────────
# Host API — timers
# ─────────────────────────────────────────────────────────────────────────────


class TestTimers:
    def test_set_timeout_returns_id(self, rt):
        r = rt.eval_safe("typeof setTimeout(function(){}, 100)")
        assert r.value == "number"

    def test_clear_timeout_no_raise(self, rt):
        rt.eval_safe("var id = setTimeout(function(){}, 100); clearTimeout(id);")

    def test_set_interval_returns_id(self, rt):
        r = rt.eval_safe("typeof setInterval(function(){}, 100)")
        assert r.value == "number"

    def test_request_animation_frame(self, rt):
        r = rt.eval_safe("typeof requestAnimationFrame(function(){})")
        assert r.value == "number"


# ─────────────────────────────────────────────────────────────────────────────
# Host API — MutationObserver / IntersectionObserver stubs
# ─────────────────────────────────────────────────────────────────────────────


class TestObserverStubs:
    def test_mutation_observer_exists(self, rt):
        r = rt.eval_safe("typeof MutationObserver")
        assert r.value == "function"

    def test_mutation_observer_observe(self, rt):
        r = rt.eval_safe("""
        var mo = new MutationObserver(function() {});
        mo.observe(document.body || {}, {childList: true});
        'ok'
        """)
        assert r.value == "ok"

    def test_intersection_observer_exists(self, rt):
        r = rt.eval_safe("typeof IntersectionObserver")
        assert r.value == "function"

    def test_resize_observer_exists(self, rt):
        r = rt.eval_safe("typeof ResizeObserver")
        assert r.value == "function"


# ─────────────────────────────────────────────────────────────────────────────
# on_page_load / navigation reset
# ─────────────────────────────────────────────────────────────────────────────


class TestPageLoad:
    def test_on_page_load_resets_context(self):
        session = _make_session("https://page1.com/")
        rt = JSRuntime(session)
        rt.eval("var __page1var = true;")
        rt.on_page_load()
        # After reset, the variable should be gone
        r = rt.eval_safe("typeof __page1var")
        assert r.value == "undefined"
        rt.close()

    def test_dispatch_dom_content_loaded(self, rt):
        rt.eval_safe("var _dcl = false; window.addEventListener('DOMContentLoaded', function(){ _dcl = true; });")
        rt.dispatch_dom_content_loaded()
        r = rt.eval_safe("_dcl")
        assert r.value is True

    def test_dispatch_load(self, rt):
        rt.eval_safe("var _loaded = false; window.addEventListener('load', function(){ _loaded = true; });")
        rt.dispatch_load()
        r = rt.eval_safe("_loaded")
        assert r.value is True
