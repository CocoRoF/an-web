"""
Host Web API implementations injected into QuickJS context.

Architecture
------------
quickjs-py only supports registering Python callables (via add_callable) and
setting primitive globals (via ctx.set). Complex browser objects like
``document``, ``window``, ``location``, etc. are built as follows:

1. **Python callback layer**: thin Python functions handle DOM reads/writes,
   network requests, and storage operations.
2. **JS shim layer**: a single JS bootstrap script constructs the full API
   surface using those callbacks, then installs it on globalThis.

This two-layer approach lets the JS shim code live in Python strings for
easy maintenance while keeping all side-effectful operations in Python.

Injection order:
    1. Register all ``_py_*`` callbacks via ctx.add_callable()
    2. Run the JS bootstrap script that builds globalThis.document,
       globalThis.window, globalThis.location, etc.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from an_web.js.bridge import marshal_document, marshal_element

if TYPE_CHECKING:
    from an_web.core.session import Session

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def install_host_api(ctx: Any, session: Session) -> None:
    """
    Install the complete host Web API into a QuickJS context.

    This is called once after Context() creation. It:
    1. Registers all Python-backed ``_py_*`` callables.
    2. Evaluates the JS bootstrap that builds the browser globals.

    Args:
        ctx:     A ``quickjs.Context`` instance.
        session: The owning Session (for DOM/network/storage access).
    """
    _register_py_callbacks(ctx, session)
    ctx.eval(_JS_BOOTSTRAP)


# ─────────────────────────────────────────────────────────────────────────────
# Python callback registration
# ─────────────────────────────────────────────────────────────────────────────


def _register_py_callbacks(ctx: Any, session: Session) -> None:
    """Register all _py_* Python functions into the QuickJS context."""

    # ── Console ──────────────────────────────────────────────────────────────
    _console_log = logging.getLogger("an_web.js.console")

    def _py_console_log(*args: Any) -> None:
        _console_log.debug(" ".join(str(a) for a in args))

    def _py_console_warn(*args: Any) -> None:
        _console_log.warning(" ".join(str(a) for a in args))

    def _py_console_error(*args: Any) -> None:
        _console_log.error(" ".join(str(a) for a in args))

    ctx.add_callable("_py_console_log", _py_console_log)
    ctx.add_callable("_py_console_warn", _py_console_warn)
    ctx.add_callable("_py_console_error", _py_console_error)

    # ── document ─────────────────────────────────────────────────────────────

    def _py_doc_meta() -> str:
        doc = getattr(session, "_current_document", None)
        return json.dumps(marshal_document(doc))

    def _py_doc_get_title() -> str:
        doc = getattr(session, "_current_document", None)
        return getattr(doc, "title", "") or ""

    def _py_doc_set_title(new_title: str) -> None:
        doc = getattr(session, "_current_document", None)
        if doc is not None:
            doc.title = new_title

    def _py_doc_get_url() -> str:
        return getattr(session, "_current_url", "about:blank") or "about:blank"

    def _py_query_selector(selector: str) -> str:
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "null"
        # Document doesn't have querySelector; use SelectorEngine
        from an_web.dom.selectors import SelectorEngine
        engine = SelectorEngine()
        # Strip any leading nodeId prefix added by element.querySelector
        sel = selector.split(" ", 1)[-1] if " " in selector else selector
        el = engine.query_selector(doc, sel)
        if el is None:
            return "null"
        return json.dumps(marshal_element(el))

    def _py_query_selector_all(selector: str) -> str:
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.selectors import SelectorEngine
        engine = SelectorEngine()
        sel = selector.split(" ", 1)[-1] if " " in selector else selector
        elements = engine.query_selector_all(doc, sel)
        return json.dumps([marshal_element(e) for e in elements])

    def _py_get_element_by_id(elem_id: str) -> str:
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "null"
        el = doc.get_element_by_id(elem_id)
        if el is None:
            return "null"
        return json.dumps(marshal_element(el))

    def _py_get_elements_by_tag(tag: str) -> str:
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        elements = [
            e for e in doc.iter_descendants()
            if isinstance(e, Element) and e.tag == tag.lower()
        ]
        return json.dumps([marshal_element(e) for e in elements])

    def _py_get_elements_by_class(class_name: str) -> str:
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        elements = [
            e for e in doc.iter_descendants()
            if isinstance(e, Element)
            and class_name in (e.attributes.get("class", "") or "").split()
        ]
        return json.dumps([marshal_element(e) for e in elements])

    def _py_get_attribute(node_id: str, attr_name: str) -> str:
        """Get attribute value for a node identified by node_id."""
        el = _find_element_by_id(session, node_id)
        if el is None:
            return "null"
        val = el.attributes.get(attr_name)
        return json.dumps(val)

    def _py_set_attribute(node_id: str, attr_name: str, value: str) -> None:
        el = _find_element_by_id(session, node_id)
        if el is not None:
            el.attributes[attr_name] = value

    def _py_get_inner_html(node_id: str) -> str:
        from an_web.js.bridge import _inner_html
        el = _find_element_by_id(session, node_id)
        if el is None:
            return ""
        return _inner_html(el)

    def _py_get_text_content(node_id: str) -> str:
        el = _find_element_by_id(session, node_id)
        if el is None:
            return ""
        return getattr(el, "text_content", "") or ""

    def _py_remove_attribute(node_id: str, attr_name: str) -> None:
        el = _find_element_by_id(session, node_id)
        if el is not None:
            el.attributes.pop(attr_name, None)

    def _py_has_attribute(node_id: str, attr_name: str) -> bool:
        el = _find_element_by_id(session, node_id)
        if el is None:
            return False
        return attr_name in el.attributes

    def _py_get_parent(node_id: str) -> str:
        """Return the parent element serialised, or 'null'."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "null"
        from an_web.dom.nodes import Element
        # Build child->parent map lazily
        target = _find_element_by_id(session, node_id)
        if target is None:
            return "null"
        # Walk tree to find parent
        for node in doc.iter_descendants():
            if isinstance(node, Element):
                for child in node.children:
                    if getattr(child, "node_id", None) == node_id:
                        return json.dumps(marshal_element(node))
        return "null"

    def _py_get_siblings(node_id: str) -> str:
        """Return {prev, next} sibling element nodeIds (or null)."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return json.dumps({"prev": None, "next": None})
        from an_web.dom.nodes import Element
        for node in doc.iter_descendants():
            if isinstance(node, Element):
                siblings = [c for c in node.children if isinstance(c, Element)]
                for i, child in enumerate(siblings):
                    if child.node_id == node_id:
                        prev = json.loads(
                            json.dumps(marshal_element(siblings[i - 1]))
                        ) if i > 0 else None
                        next_ = json.loads(
                            json.dumps(marshal_element(siblings[i + 1]))
                        ) if i < len(siblings) - 1 else None
                        return json.dumps({"prev": prev, "next": next_})
        return json.dumps({"prev": None, "next": None})

    def _py_query_selector_in(node_id: str, selector: str) -> str:
        """querySelector scoped to the subtree of node with node_id."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "null"
        from an_web.dom.nodes import Element
        from an_web.dom.selectors import SelectorEngine
        root = _find_element_by_id(session, node_id)
        if root is None:
            return "null"
        # Create a temporary search context over root's descendants
        engine = SelectorEngine()
        # Use the selector engine on full doc then filter to root's subtree
        descendants = {n.node_id for n in root.iter_descendants() if isinstance(n, Element)}
        all_matches = engine.query_selector_all(doc, selector)
        for el in all_matches:
            if el.node_id in descendants:
                return json.dumps(marshal_element(el))
        return "null"

    def _py_query_selector_all_in(node_id: str, selector: str) -> str:
        """querySelectorAll scoped to subtree of node with node_id."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        from an_web.dom.selectors import SelectorEngine
        root = _find_element_by_id(session, node_id)
        if root is None:
            return "[]"
        engine = SelectorEngine()
        descendants = {n.node_id for n in root.iter_descendants() if isinstance(n, Element)}
        all_matches = engine.query_selector_all(doc, selector)
        scoped = [el for el in all_matches if el.node_id in descendants]
        return json.dumps([marshal_element(e) for e in scoped])

    def _py_get_forms() -> str:
        """Return all <form> elements."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        forms = [
            e for e in doc.iter_descendants()
            if isinstance(e, Element) and e.tag == "form"
        ]
        return json.dumps([marshal_element(f) for f in forms])

    def _py_get_links() -> str:
        """Return all <a href> elements."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        links = [
            el for el in doc.iter_descendants()
            if isinstance(el, Element) and el.tag == "a" and "href" in el.attributes
        ]
        return json.dumps([marshal_element(el) for el in links])

    def _py_get_images() -> str:
        """Return all <img> elements."""
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return "[]"
        from an_web.dom.nodes import Element
        imgs = [
            e for e in doc.iter_descendants()
            if isinstance(e, Element) and e.tag == "img"
        ]
        return json.dumps([marshal_element(i) for i in imgs])

    ctx.add_callable("_py_doc_meta", _py_doc_meta)
    ctx.add_callable("_py_doc_get_title", _py_doc_get_title)
    ctx.add_callable("_py_doc_set_title", _py_doc_set_title)
    ctx.add_callable("_py_doc_get_url", _py_doc_get_url)
    ctx.add_callable("_py_query_selector", _py_query_selector)
    ctx.add_callable("_py_query_selector_all", _py_query_selector_all)
    ctx.add_callable("_py_get_element_by_id", _py_get_element_by_id)
    ctx.add_callable("_py_get_elements_by_tag", _py_get_elements_by_tag)
    ctx.add_callable("_py_get_elements_by_class", _py_get_elements_by_class)
    ctx.add_callable("_py_get_attribute", _py_get_attribute)
    ctx.add_callable("_py_set_attribute", _py_set_attribute)
    ctx.add_callable("_py_remove_attribute", _py_remove_attribute)
    ctx.add_callable("_py_has_attribute", _py_has_attribute)
    ctx.add_callable("_py_get_inner_html", _py_get_inner_html)
    ctx.add_callable("_py_get_text_content", _py_get_text_content)
    ctx.add_callable("_py_get_parent", _py_get_parent)
    ctx.add_callable("_py_get_siblings", _py_get_siblings)
    ctx.add_callable("_py_query_selector_in", _py_query_selector_in)
    ctx.add_callable("_py_query_selector_all_in", _py_query_selector_all_in)
    ctx.add_callable("_py_get_forms", _py_get_forms)
    ctx.add_callable("_py_get_links", _py_get_links)
    ctx.add_callable("_py_get_images", _py_get_images)

    # ── window / location / navigator ────────────────────────────────────────

    def _py_win_href() -> str:
        return getattr(session, "_current_url", "about:blank") or "about:blank"

    def _py_win_navigate(url: str) -> None:
        """Synchronous navigate — stores pending navigation for session."""
        session._pending_js_navigation = url

    def _py_history_length() -> int:
        return len(getattr(session, "_history", []))

    ctx.add_callable("_py_win_href", _py_win_href)
    ctx.add_callable("_py_win_navigate", _py_win_navigate)
    ctx.add_callable("_py_history_length", _py_history_length)

    # ── localStorage / sessionStorage ────────────────────────────────────────

    def _py_storage_get(store_name: str, key: str) -> str:
        store = _get_storage(session, store_name)
        val = store.get(key)
        return json.dumps(val)

    def _py_storage_set(store_name: str, key: str, value: str) -> None:
        store = _get_storage(session, store_name)
        store[key] = value

    def _py_storage_remove(store_name: str, key: str) -> None:
        store = _get_storage(session, store_name)
        store.pop(key, None)

    def _py_storage_clear(store_name: str) -> None:
        store = _get_storage(session, store_name)
        store.clear()

    def _py_storage_key(store_name: str, index: int) -> str:
        store = _get_storage(session, store_name)
        keys = list(store.keys())
        return json.dumps(keys[index] if index < len(keys) else None)

    def _py_storage_length(store_name: str) -> int:
        store = _get_storage(session, store_name)
        return len(store)

    ctx.add_callable("_py_storage_get", _py_storage_get)
    ctx.add_callable("_py_storage_set", _py_storage_set)
    ctx.add_callable("_py_storage_remove", _py_storage_remove)
    ctx.add_callable("_py_storage_clear", _py_storage_clear)
    ctx.add_callable("_py_storage_key", _py_storage_key)
    ctx.add_callable("_py_storage_length", _py_storage_length)

    # ── Timers ────────────────────────────────────────────────────────────────
    # JS timers are stored in session._pending_timers for the scheduler
    # to drain. We use a cooperative model: setTimeout stores the callback
    # id + delay, and drain_microtasks() fires any whose delay <= elapsed.

    _timer_registry: dict[int, tuple[float, str]] = {}
    _timer_counter: list[int] = [0]
    _timer_callbacks: dict[int, Any] = {}  # id -> JS function name or None

    def _py_set_timeout_ms(delay_ms: float, callback_key: str) -> int:
        _timer_counter[0] += 1
        tid = _timer_counter[0]
        fire_at = time.monotonic() + (delay_ms / 1000.0)
        _timer_registry[tid] = (fire_at, callback_key)
        # Store on session so JSRuntime can fire them
        if not hasattr(session, "_js_timers"):
            session._js_timers = {}  # type: ignore[attr-defined]
        session._js_timers[tid] = (fire_at, callback_key)  # type: ignore[attr-defined]
        return tid

    def _py_clear_timeout(timer_id: int) -> None:
        _timer_registry.pop(timer_id, None)
        timers = getattr(session, "_js_timers", {})
        timers.pop(timer_id, None)

    def _py_get_fired_timers() -> str:
        """Return list of timer_ids whose fire_at <= now. Used by drain loop."""
        now = time.monotonic()
        fired = [
            tid for tid, (fire_at, _) in list(_timer_registry.items())
            if fire_at <= now
        ]
        for tid in fired:
            _timer_registry.pop(tid, None)
            timers = getattr(session, "_js_timers", {})
            timers.pop(tid, None)
        return json.dumps(fired)

    ctx.add_callable("_py_set_timeout_ms", _py_set_timeout_ms)
    ctx.add_callable("_py_clear_timeout", _py_clear_timeout)
    ctx.add_callable("_py_get_fired_timers", _py_get_fired_timers)

    # ── fetch (sync shim using asyncio.run_until_complete) ───────────────────
    # Real async fetch is handled by the session's network layer; this provides
    # a synchronous bridge for script-triggered fetches within JS eval.

    def _py_fetch_sync(url: str, method: str, body_json: str, headers_json: str) -> str:
        """
        Synchronous network request from JS. Returns JSON response dict.
        This is a best-effort sync bridge — real async requests go through
        the session's NetworkClient outside of JS eval.
        """
        import asyncio
        network = getattr(session, "network", None)
        if network is None:
            return json.dumps({"ok": False, "status": 0, "text": "", "error": "no_network"})
        try:
            headers = json.loads(headers_json) if headers_json else {}
            body = json.loads(body_json) if body_json else None

            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't use run_until_complete inside running loop
                # Return a placeholder — the real fetch is async
                return json.dumps({
                    "ok": False, "status": 0, "text": "",
                    "error": "async_context_fetch_not_supported",
                })

            if method.upper() == "GET":
                response = loop.run_until_complete(
                    network.get(url, headers=headers)
                )
            else:
                response = loop.run_until_complete(
                    network.post(url, json=body, headers=headers)
                )

            return json.dumps({
                "ok": response.ok,
                "status": response.status,
                "text": response.text,
                "headers": response.headers,
                "url": response.url,
            })
        except Exception as exc:
            log.debug("_py_fetch_sync error: %s", exc)
            return json.dumps({"ok": False, "status": 0, "text": "", "error": str(exc)})

    ctx.add_callable("_py_fetch_sync", _py_fetch_sync)

    # ── Date.now() shim ───────────────────────────────────────────────────────
    # QuickJS has Date built-in; no override needed unless we want determinism.

    # ── Performance.now() ─────────────────────────────────────────────────────
    _start_time = time.monotonic()

    def _py_perf_now() -> float:
        return (time.monotonic() - _start_time) * 1000.0

    ctx.add_callable("_py_perf_now", _py_perf_now)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _find_element_by_id(session: Any, node_id: str) -> Any:
    """Locate a DOM Element by its internal node_id."""
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return None
    from an_web.dom.nodes import Element
    for node in doc.iter_descendants():
        if isinstance(node, Element) and node.node_id == node_id:
            return node
    return None


def _get_storage(session: Any, store_name: str) -> dict[str, str]:
    """Return the appropriate storage dict from the session."""
    attr = "_local_storage" if store_name == "local" else "_session_storage"
    if not hasattr(session, attr):
        setattr(session, attr, {})
    return getattr(session, attr)


# ─────────────────────────────────────────────────────────────────────────────
# JS bootstrap script
# ─────────────────────────────────────────────────────────────────────────────

_JS_BOOTSTRAP = r"""
'use strict';

// ── Utilities ────────────────────────────────────────────────────────────────

function _safeParse(s) {
    if (s === null || s === undefined) return null;
    if (s === 'null') return null;
    try { return JSON.parse(s); } catch(e) { return s; }
}

// ── console ───────────────────────────────────────────────────────────────────

var console = (function() {
    function _fmt(args) {
        return Array.prototype.slice.call(args).map(function(a) {
            if (typeof a === 'object') try { return JSON.stringify(a); } catch(e) { return String(a); }
            return String(a);
        }).join(' ');
    }
    return {
        log:   function() { _py_console_log(_fmt(arguments)); },
        warn:  function() { _py_console_warn(_fmt(arguments)); },
        error: function() { _py_console_error(_fmt(arguments)); },
        info:  function() { _py_console_log(_fmt(arguments)); },
        debug: function() { _py_console_log(_fmt(arguments)); },
        trace: function() { _py_console_log(_fmt(arguments)); },
        assert: function(cond) {
            if (!cond) {
                var msg = Array.prototype.slice.call(arguments, 1);
                _py_console_error('Assertion failed: ' + _fmt(msg));
            }
        },
        group: function() {},
        groupEnd: function() {},
        time: function() {},
        timeEnd: function() {},
    };
})();

// ── EventTarget mixin ─────────────────────────────────────────────────────────

function EventTarget() {
    this._listeners = {};
}
EventTarget.prototype.addEventListener = function(type, fn, options) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
};
EventTarget.prototype.removeEventListener = function(type, fn) {
    var list = this._listeners[type];
    if (!list) return;
    var idx = list.indexOf(fn);
    if (idx >= 0) list.splice(idx, 1);
};
EventTarget.prototype.dispatchEvent = function(event) {
    var type = typeof event === 'string' ? event : event.type;
    var list = (this._listeners[type] || []).slice();
    for (var i = 0; i < list.length; i++) {
        try { list[i].call(this, event); } catch(e) {}
    }
    return true;
};

// ── Element proxy ─────────────────────────────────────────────────────────────

function _makeElement(data) {
    if (!data) return null;
    var el = Object.create(EventTarget.prototype);
    el._listeners = {};
    el.nodeId       = data.nodeId || '';
    el.nodeType     = data.nodeType !== undefined ? data.nodeType : 1;
    el.tagName      = (data.tagName || data.tag || '').toUpperCase();
    el.localName    = (data.tag || data.tagName || '').toLowerCase();
    el.id           = data.id || '';
    el.className    = data.className || '';
    el.textContent  = data.textContent || '';
    el.innerHTML    = data.innerHTML || '';
    el._attributes  = data.attributes || {};
    el._children    = (data.children || []).map(_makeElement);

    el.getAttribute = function(name) {
        return el._attributes[name] !== undefined ? el._attributes[name] : null;
    };
    el.setAttribute = function(name, value) {
        el._attributes[name] = String(value);
        _py_set_attribute(el.nodeId, name, String(value));
    };
    el.hasAttribute = function(name) {
        return el._attributes[name] !== undefined;
    };
    el.removeAttribute = function(name) {
        delete el._attributes[name];
        _py_remove_attribute(el.nodeId, name);
    };
    el.hasAttribute = function(name) {
        return _py_has_attribute(el.nodeId, name);
    };
    el.toggleAttribute = function(name, force) {
        var has = el.hasAttribute(name);
        if (force === undefined ? has : force) {
            el.removeAttribute(name);
            return false;
        } else {
            el.setAttribute(name, '');
            return true;
        }
    };

    // classList
    el.classList = (function() {
        function _getClasses() {
            return (el._attributes['class'] || '').split(/\s+/).filter(Boolean);
        }
        function _setClasses(arr) {
            var v = arr.join(' ');
            el._attributes['class'] = v;
            _py_set_attribute(el.nodeId, 'class', v);
            el.className = v;
        }
        return {
            contains: function(cls) { return _getClasses().indexOf(cls) >= 0; },
            add: function() {
                var cls = _getClasses();
                for (var i = 0; i < arguments.length; i++) {
                    if (cls.indexOf(arguments[i]) < 0) cls.push(arguments[i]);
                }
                _setClasses(cls);
            },
            remove: function() {
                var remove = Array.prototype.slice.call(arguments);
                _setClasses(_getClasses().filter(function(c) { return remove.indexOf(c) < 0; }));
            },
            toggle: function(cls, force) {
                var has = _getClasses().indexOf(cls) >= 0;
                if (force === undefined ? has : !force) {
                    this.remove(cls); return false;
                } else {
                    this.add(cls); return true;
                }
            },
            replace: function(old, next) {
                var cls = _getClasses();
                var i = cls.indexOf(old);
                if (i >= 0) { cls[i] = next; _setClasses(cls); return true; }
                return false;
            },
            toString: function() { return _getClasses().join(' '); },
            get length() { return _getClasses().length; },
            item: function(i) { return _getClasses()[i] || null; },
        };
    })();

    // dataset — maps data-* attributes
    el.dataset = new Proxy({}, {
        get: function(t, prop) {
            var attr = 'data-' + prop.replace(/([A-Z])/g, '-$1').toLowerCase();
            return el._attributes[attr];
        },
        set: function(t, prop, value) {
            var attr = 'data-' + prop.replace(/([A-Z])/g, '-$1').toLowerCase();
            el.setAttribute(attr, value);
            return true;
        },
    });

    // value property — reads/writes 'value' attribute for form inputs
    Object.defineProperty(el, 'value', {
        get: function() {
            return el._attributes['value'] !== undefined
                ? el._attributes['value']
                : el.textContent;
        },
        set: function(v) { el.setAttribute('value', String(v)); },
    });

    // checked property
    Object.defineProperty(el, 'checked', {
        get: function() { return 'checked' in el._attributes; },
        set: function(v) {
            if (v) el.setAttribute('checked', '');
            else el.removeAttribute('checked');
        },
    });

    // disabled property
    Object.defineProperty(el, 'disabled', {
        get: function() { return 'disabled' in el._attributes; },
        set: function(v) {
            if (v) el.setAttribute('disabled', '');
            else el.removeAttribute('disabled');
        },
    });

    // selected property (for <option>)
    Object.defineProperty(el, 'selected', {
        get: function() { return 'selected' in el._attributes; },
        set: function(v) {
            if (v) el.setAttribute('selected', '');
            else el.removeAttribute('selected');
        },
    });

    // href / src shortcuts
    Object.defineProperty(el, 'href', {
        get: function() { return el._attributes['href'] || ''; },
        set: function(v) { el.setAttribute('href', v); },
    });
    Object.defineProperty(el, 'src', {
        get: function() { return el._attributes['src'] || ''; },
        set: function(v) { el.setAttribute('src', v); },
    });
    Object.defineProperty(el, 'name', {
        get: function() { return el._attributes['name'] || ''; },
        set: function(v) { el.setAttribute('name', v); },
    });
    Object.defineProperty(el, 'type', {
        get: function() { return el._attributes['type'] || 'text'; },
        set: function(v) { el.setAttribute('type', v); },
    });
    Object.defineProperty(el, 'placeholder', {
        get: function() { return el._attributes['placeholder'] || ''; },
        set: function(v) { el.setAttribute('placeholder', v); },
    });

    // parentElement — lazy Python lookup
    Object.defineProperty(el, 'parentElement', {
        get: function() {
            var raw = _safeParse(_py_get_parent(el.nodeId));
            return raw ? _makeElement(raw) : null;
        },
    });
    Object.defineProperty(el, 'parentNode', {
        get: function() { return el.parentElement; },
    });

    // next/previous sibling
    Object.defineProperty(el, 'nextElementSibling', {
        get: function() {
            var s = _safeParse(_py_get_siblings(el.nodeId));
            return s && s.next ? _makeElement(s.next) : null;
        },
    });
    Object.defineProperty(el, 'previousElementSibling', {
        get: function() {
            var s = _safeParse(_py_get_siblings(el.nodeId));
            return s && s.prev ? _makeElement(s.prev) : null;
        },
    });
    Object.defineProperty(el, 'nextSibling', {
        get: function() { return el.nextElementSibling; },
    });
    Object.defineProperty(el, 'previousSibling', {
        get: function() { return el.previousElementSibling; },
    });

    el.matches = function(selector) {
        // Approximate implementation for common cases
        selector = selector.trim();
        if (selector === '*') return true;
        if (selector[0] === '#') return el.id === selector.slice(1);
        if (selector[0] === '.') return (' ' + el.className + ' ').indexOf(' ' + selector.slice(1) + ' ') >= 0;
        return el.localName === selector.toLowerCase();
    };
    el.closest = function(selector) {
        var current = el;
        while (current) {
            if (current.matches && current.matches(selector)) return current;
            current = current.parentElement;
        }
        return null;
    };

    // querySelector/querySelectorAll on element — scoped to subtree
    el.querySelector = function(sel) {
        var raw = _safeParse(_py_query_selector_in(el.nodeId, sel));
        return raw ? _makeElement(raw) : null;
    };
    el.querySelectorAll = function(sel) {
        return (_safeParse(_py_query_selector_all_in(el.nodeId, sel)) || []).map(_makeElement);
    };
    el.getElementsByTagName = function(tag) {
        return (_safeParse(_py_get_elements_by_tag(tag)) || []).filter(function(d) {
            // filter to subtree — approximate via children check
            return true;
        }).map(_makeElement);
    };
    el.getElementsByClassName = function(cls) {
        return el.querySelectorAll('.' + cls);
    };

    // children array access
    Object.defineProperty(el, 'children', {
        get: function() { return el._children; }
    });
    Object.defineProperty(el, 'childNodes', {
        get: function() { return el._children; }
    });
    Object.defineProperty(el, 'firstChild', {
        get: function() { return el._children[0] || null; }
    });
    Object.defineProperty(el, 'lastChild', {
        get: function() { return el._children[el._children.length - 1] || null; }
    });
    Object.defineProperty(el, 'childElementCount', {
        get: function() { return el._children.length; }
    });

    // Style stub
    el.style = {};
    Object.defineProperty(el, 'hidden', {
        get: function() { return el.getAttribute('hidden') !== null || el._attributes['style'] && /display\s*:\s*none/.test(el._attributes['style']); },
        set: function(v) { if (v) el._attributes['hidden'] = ''; else delete el._attributes['hidden']; }
    });

    // focus/blur/click stubs
    el.focus = function() {};
    el.blur  = function() {};
    el.click = function() {};
    el.submit = function() {};
    el.reset  = function() {};
    el.select = function() {};

    // DOM mutation stubs (no live DOM rewrite in AN-Web)
    el.appendChild  = function(child) { return child; };
    el.removeChild  = function(child) { return child; };
    el.insertBefore = function(newNode, ref) { return newNode; };
    el.replaceChild = function(newNode, old) { return old; };
    el.cloneNode    = function(deep) { return _makeElement(data); };
    el.remove       = function() {};
    el.before       = function() {};
    el.after        = function() {};
    el.prepend      = function() {};
    el.append       = function() {};
    el.replaceWith  = function() {};

    el.insertAdjacentHTML = function(pos, html) {};
    el.insertAdjacentText = function(pos, text) {};
    el.insertAdjacentElement = function(pos, el2) { return el2; };

    el.contains = function(other) {
        if (!other) return false;
        var kids = el._children;
        for (var i = 0; i < kids.length; i++) {
            if (kids[i].nodeId === other.nodeId) return true;
            if (kids[i].contains && kids[i].contains(other)) return true;
        }
        return false;
    };

    el.scrollIntoView = function() {};
    el.scrollTo = function() {};
    el.scrollBy = function() {};
    el.getBoundingClientRect = function() {
        return { top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: function() { return this; } };
    };
    el.getClientRects = function() { return []; };
    el.offsetParent = null;
    el.offsetTop = 0; el.offsetLeft = 0;
    el.offsetWidth = 0; el.offsetHeight = 0;
    el.scrollWidth = 0; el.scrollHeight = 0;
    el.scrollTop = 0; el.scrollLeft = 0;
    el.clientWidth = 0; el.clientHeight = 0;

    return el;
}

// ── document ─────────────────────────────────────────────────────────────────

var document = (function() {
    var doc = Object.create(EventTarget.prototype);
    doc._listeners = {};
    doc.nodeType = 9;
    doc.readyState = 'complete';

    Object.defineProperty(doc, 'title', {
        get: function() { return _py_doc_get_title(); },
        set: function(v) { _py_doc_set_title(String(v)); },
    });
    Object.defineProperty(doc, 'URL', {
        get: function() { return _py_doc_get_url(); },
    });
    Object.defineProperty(doc, 'documentURI', {
        get: function() { return _py_doc_get_url(); },
    });
    Object.defineProperty(doc, 'location', {
        get: function() { return window.location; },
    });
    Object.defineProperty(doc, 'domain', {
        get: function() {
            try { return new URL(_py_doc_get_url()).hostname; } catch(e) { return ''; }
        },
    });
    Object.defineProperty(doc, 'cookie', {
        get: function() { return ''; },   // TODO: hook into CookieJar
        set: function(v) {},
    });
    Object.defineProperty(doc, 'body', {
        get: function() { return doc.querySelector('body'); },
    });
    Object.defineProperty(doc, 'head', {
        get: function() { return doc.querySelector('head'); },
    });
    Object.defineProperty(doc, 'documentElement', {
        get: function() { return doc.querySelector('html') || doc.querySelector('body'); },
    });

    doc.querySelector = function(sel) {
        var raw = _safeParse(_py_query_selector(sel));
        return raw ? _makeElement(raw) : null;
    };
    doc.querySelectorAll = function(sel) {
        return (_safeParse(_py_query_selector_all(sel)) || []).map(_makeElement);
    };
    doc.getElementById = function(id) {
        var raw = _safeParse(_py_get_element_by_id(id));
        return raw ? _makeElement(raw) : null;
    };
    doc.getElementsByTagName = function(tag) {
        return (_safeParse(_py_get_elements_by_tag(tag)) || []).map(_makeElement);
    };
    doc.getElementsByClassName = function(cls) {
        return (_safeParse(_py_get_elements_by_class(cls)) || []).map(_makeElement);
    };
    doc.getElementsByName = function(name) {
        return doc.querySelectorAll('[name="' + name + '"]');
    };

    // createElement returns a lightweight stub (not in DOM)
    doc.createElement = function(tag) {
        return _makeElement({
            nodeId: '_new_' + tag + '_' + Date.now(),
            tag: tag,
            tagName: tag.toUpperCase(),
            nodeType: 1,
            id: '', className: '', textContent: '', innerHTML: '',
            attributes: {}, children: []
        });
    };
    doc.createTextNode = function(text) {
        return { nodeType: 3, data: text, textContent: text, nodeValue: text };
    };
    doc.createDocumentFragment = function() {
        return _makeElement({ nodeId: '_frag_' + Date.now(), tag: '', tagName: '', nodeType: 11, attributes: {}, children: [] });
    };

    // Event helpers
    doc.createEvent = function(type) {
        return { type: '', bubbles: false, cancelable: false, initEvent: function(t) { this.type = t; } };
    };

    // Write stub — AN-Web ignores document.write
    doc.write = function(html) {};
    doc.writeln = function(html) {};
    doc.open = function() {};
    doc.close = function() {};

    // Convenience collections
    Object.defineProperty(doc, 'forms', {
        get: function() { return (_safeParse(_py_get_forms()) || []).map(_makeElement); }
    });
    Object.defineProperty(doc, 'links', {
        get: function() { return (_safeParse(_py_get_links()) || []).map(_makeElement); }
    });
    Object.defineProperty(doc, 'images', {
        get: function() { return (_safeParse(_py_get_images()) || []).map(_makeElement); }
    });
    Object.defineProperty(doc, 'scripts', {
        get: function() { return doc.querySelectorAll('script'); }
    });
    Object.defineProperty(doc, 'styleSheets', {
        get: function() { return []; }
    });

    // activeElement — return focused element or body
    Object.defineProperty(doc, 'activeElement', {
        get: function() { return doc.body; }
    });

    // hasFocus
    doc.hasFocus = function() { return true; };

    // contains
    doc.contains = function(node) { return node !== null && node !== undefined; };

    // importNode / adoptNode stubs
    doc.importNode = function(node, deep) { return node; };
    doc.adoptNode   = function(node) { return node; };

    return doc;
})();

// ── location ──────────────────────────────────────────────────────────────────

var location = (function() {
    var loc = {};
    function _getUrl() { return _py_win_href(); }
    function _parsed() {
        try { return new URL(_getUrl()); } catch(e) { return null; }
    }

    Object.defineProperty(loc, 'href', {
        get: function() { return _getUrl(); },
        set: function(v) { _py_win_navigate(v); },
    });
    Object.defineProperty(loc, 'pathname', {
        get: function() { var u = _parsed(); return u ? u.pathname : '/'; }
    });
    Object.defineProperty(loc, 'search', {
        get: function() { var u = _parsed(); return u ? u.search : ''; }
    });
    Object.defineProperty(loc, 'hash', {
        get: function() { var u = _parsed(); return u ? u.hash : ''; }
    });
    Object.defineProperty(loc, 'hostname', {
        get: function() { var u = _parsed(); return u ? u.hostname : ''; }
    });
    Object.defineProperty(loc, 'host', {
        get: function() { var u = _parsed(); return u ? u.host : ''; }
    });
    Object.defineProperty(loc, 'protocol', {
        get: function() { var u = _parsed(); return u ? u.protocol : ''; }
    });
    Object.defineProperty(loc, 'origin', {
        get: function() { var u = _parsed(); return u ? u.origin : ''; }
    });
    loc.assign   = function(url) { _py_win_navigate(url); };
    loc.replace  = function(url) { _py_win_navigate(url); };
    loc.reload   = function() {};
    loc.toString = function() { return _getUrl(); };
    return loc;
})();

// ── history ───────────────────────────────────────────────────────────────────

var history = (function() {
    var h = Object.create(EventTarget.prototype);
    h._listeners = {};
    Object.defineProperty(h, 'length', {
        get: function() { return _py_history_length(); }
    });
    h.pushState    = function(state, title, url) { if (url) _py_win_navigate(url); };
    h.replaceState = function(state, title, url) { if (url) _py_win_navigate(url); };
    h.back         = function() {};
    h.forward      = function() {};
    h.go           = function() {};
    return h;
})();

// ── navigator ─────────────────────────────────────────────────────────────────

var navigator = {
    userAgent:   'Mozilla/5.0 (AN-Web/1.0; AI Agent) Python/3.12',
    platform:    'Linux',
    language:    'en-US',
    languages:   ['en-US', 'en'],
    onLine:      true,
    cookieEnabled: true,
    doNotTrack:  null,
    hardwareConcurrency: 4,
    maxTouchPoints: 0,
    vendor: 'AN-Web',
    appName: 'AN-Web',
    appVersion: '1.0',
    geolocation: null,
    clipboard: null,
    serviceWorker: { register: function() { return Promise.reject(new Error('not supported')); } },
};

// ── screen ────────────────────────────────────────────────────────────────────

var screen = {
    width: 1280, height: 800,
    availWidth: 1280, availHeight: 800,
    colorDepth: 24, pixelDepth: 24,
};

// ── localStorage / sessionStorage ────────────────────────────────────────────

function _makeStorage(storeName) {
    var store = {};
    Object.defineProperty(store, 'length', {
        get: function() { return _py_storage_length(storeName); }
    });
    store.getItem = function(key) {
        return _safeParse(_py_storage_get(storeName, key));
    };
    store.setItem = function(key, value) {
        _py_storage_set(storeName, String(key), String(value));
    };
    store.removeItem = function(key) {
        _py_storage_remove(storeName, key);
    };
    store.clear = function() {
        _py_storage_clear(storeName);
    };
    store.key = function(index) {
        return _safeParse(_py_storage_key(storeName, index));
    };
    return store;
}

var localStorage    = _makeStorage('local');
var sessionStorage  = _makeStorage('session');

// ── Timers ────────────────────────────────────────────────────────────────────
// Cooperative timer model: callbacks are stored in a JS map and fired by
// JSRuntime.drain_timers() after each microtask drain.

var _timerCallbacks = {};
var _intervalCallbacks = {};

function setTimeout(fn, delay) {
    if (typeof delay !== 'number') delay = 0;
    var key = '_t' + Date.now() + '_' + Math.random();
    _timerCallbacks[key] = fn;
    var id = _py_set_timeout_ms(delay, key);
    return id;
}
function clearTimeout(id) {
    _py_clear_timeout(id);
}
function setInterval(fn, delay) {
    // For simplicity, setInterval is treated as a one-shot (like setTimeout)
    // Real repeating intervals would require scheduler support
    return setTimeout(fn, delay || 0);
}
function clearInterval(id) {
    clearTimeout(id);
}
function queueMicrotask(fn) {
    Promise.resolve().then(fn);
}
function requestAnimationFrame(fn) {
    return setTimeout(fn, 16);
}
function cancelAnimationFrame(id) {
    clearTimeout(id);
}

// Fire all timers that the Python layer has marked as ready
function _fireReadyTimers() {
    var fired = JSON.parse(_py_get_fired_timers());
    // fired is a list of timer IDs — but we keyed by string key, not id
    // This is a limitation; for now JS timers are best-effort
}

// ── fetch ─────────────────────────────────────────────────────────────────────

function fetch(url, options) {
    options = options || {};
    var method = (options.method || 'GET').toUpperCase();
    var body   = options.body ? JSON.stringify(options.body) : 'null';
    var headers = options.headers ? JSON.stringify(options.headers) : 'null';

    return new Promise(function(resolve, reject) {
        try {
            var raw = _py_fetch_sync(url, method, body, headers);
            var data = JSON.parse(raw);
            if (data.error && data.error !== 'async_context_fetch_not_supported') {
                reject(new TypeError('fetch failed: ' + data.error));
                return;
            }
            var resp = {
                ok:     data.ok,
                status: data.status,
                url:    data.url || url,
                headers: { get: function(n) { return (data.headers || {})[n] || null; } },
                text:  function() { return Promise.resolve(data.text || ''); },
                json:  function() { return Promise.resolve(JSON.parse(data.text || 'null')); },
                blob:  function() { return Promise.resolve(new Blob([data.text || ''])); },
            };
            resolve(resp);
        } catch(e) {
            reject(new TypeError('fetch failed: ' + e.message));
        }
    });
}

// ── XMLHttpRequest stub ───────────────────────────────────────────────────────

function XMLHttpRequest() {
    this.readyState = 0;
    this.status = 0;
    this.statusText = '';
    this.responseText = '';
    this.response = null;
    this.responseType = '';
    this._headers = {};
    this._method = 'GET';
    this._url = '';
    this.onload = null;
    this.onerror = null;
    this.onreadystatechange = null;
}
XMLHttpRequest.prototype.open = function(method, url) {
    this._method = method;
    this._url = url;
    this.readyState = 1;
};
XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    this._headers[name] = value;
};
XMLHttpRequest.prototype.send = function(body) {
    var self = this;
    var raw;
    try {
        raw = _py_fetch_sync(
            self._url, self._method,
            body ? JSON.stringify(body) : 'null',
            JSON.stringify(self._headers)
        );
        var data = JSON.parse(raw);
        self.readyState = 4;
        self.status     = data.status || 0;
        self.statusText = data.ok ? 'OK' : 'Error';
        self.responseText = data.text || '';
        self.response    = self.responseText;
    } catch(e) {
        self.readyState = 4;
        self.status = 0;
    }
    if (self.onreadystatechange) try { self.onreadystatechange(); } catch(e) {}
    if (self.readyState === 4) {
        if (self.status >= 200 && self.status < 300 && self.onload) {
            try { self.onload({ target: self }); } catch(e) {}
        } else if (self.onerror) {
            try { self.onerror(); } catch(e) {}
        }
    }
};
XMLHttpRequest.prototype.abort = function() {};
XMLHttpRequest.prototype.getResponseHeader = function(name) { return null; };
XMLHttpRequest.prototype.getAllResponseHeaders = function() { return ''; };
XMLHttpRequest.UNSENT = 0;
XMLHttpRequest.OPENED = 1;
XMLHttpRequest.HEADERS_RECEIVED = 2;
XMLHttpRequest.LOADING = 3;
XMLHttpRequest.DONE = 4;

// ── performance ───────────────────────────────────────────────────────────────

var performance = {
    now: function() { return _py_perf_now(); },
    mark: function() {},
    measure: function() {},
    getEntriesByName: function() { return []; },
    getEntriesByType: function() { return []; },
    clearMarks: function() {},
    clearMeasures: function() {},
    timing: { navigationStart: Date.now() },
};

// ── window (globalThis alias + extras) ───────────────────────────────────────

var window = globalThis;
window.document     = document;
window.location     = location;
window.history      = history;
window.navigator    = navigator;
window.screen       = screen;
window.localStorage = localStorage;
window.sessionStorage = sessionStorage;
window.console      = console;
window.performance  = performance;
window.XMLHttpRequest = XMLHttpRequest;
window.fetch        = fetch;
window.setTimeout   = setTimeout;
window.clearTimeout = clearTimeout;
window.setInterval  = setInterval;
window.clearInterval = clearInterval;
window.queueMicrotask = queueMicrotask;
window.requestAnimationFrame = requestAnimationFrame;
window.cancelAnimationFrame  = cancelAnimationFrame;
window.EventTarget  = EventTarget;
window.self         = window;
window.top          = window;
window.parent       = window;
window.frames       = window;
window.frameElement = null;
window.opener       = null;
window.closed       = false;

window.alert   = function(msg) { _py_console_log('[alert] ' + msg); };
window.confirm = function(msg) { _py_console_log('[confirm] ' + msg); return true; };
window.prompt  = function(msg, def_) { _py_console_log('[prompt] ' + msg); return def_ || ''; };

window.getComputedStyle = function(el, pseudo) {
    return {
        getPropertyValue: function(prop) { return ''; },
        display: 'block',
        visibility: 'visible',
    };
};
window.matchMedia = function(query) {
    return { matches: false, media: query, addListener: function(){}, removeListener: function(){} };
};

// URL / URLSearchParams polyfill (QuickJS has no browser globals)
var URL = (typeof URL !== 'undefined') ? URL : (function() {
    function URL(href, base) {
        if (base && !/^[a-z][a-z0-9+\-.]*:/i.test(href)) {
            // naive relative resolver
            var b = new URL(base);
            if (href.charAt(0) === '/') href = b.protocol + '//' + b.host + href;
            else href = b.href.replace(/\/[^\/]*$/, '/') + href;
        }
        var m = href.match(/^([a-z][a-z0-9+\-.]*:)\/\/([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?/i) || [];
        this.href     = href;
        this.protocol = m[1] || '';
        this.host     = m[2] || '';
        this.hostname = (m[2] || '').replace(/:.*$/, '');
        this.port     = ((m[2] || '').match(/:(\d+)$/) || [])[1] || '';
        this.pathname = m[3] || '/';
        this.search   = m[4] || '';
        this.hash     = m[5] || '';
        this.origin   = this.protocol && this.host ? this.protocol + '//' + this.host : 'null';
    }
    URL.prototype.toString = function() { return this.href; };
    return URL;
})();

var URLSearchParams = (typeof URLSearchParams !== 'undefined') ? URLSearchParams : (function() {
    function URLSearchParams(init) {
        this._params = {};
        if (typeof init === 'string') {
            var pairs = init.replace(/^\?/, '').split('&');
            for (var i = 0; i < pairs.length; i++) {
                var kv = pairs[i].split('=');
                if (kv[0]) this._params[decodeURIComponent(kv[0])] = decodeURIComponent(kv[1] || '');
            }
        }
    }
    URLSearchParams.prototype.get = function(k) { return this._params[k] !== undefined ? this._params[k] : null; };
    URLSearchParams.prototype.set = function(k, v) { this._params[k] = String(v); };
    URLSearchParams.prototype.has = function(k) { return k in this._params; };
    URLSearchParams.prototype.toString = function() {
        return Object.keys(this._params).map(function(k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(this._params[k]);
        }, this).join('&');
    };
    return URLSearchParams;
})();

var Blob = (typeof Blob !== 'undefined') ? Blob : function Blob(parts, opts) {
    this.size = (parts || []).reduce(function(s, p) { return s + String(p).length; }, 0);
    this.type = (opts && opts.type) || '';
    this._data = (parts || []).join('');
    this.text  = function() { return Promise.resolve(this._data); };
    this.arrayBuffer = function() { return Promise.resolve(new ArrayBuffer(0)); };
};

window.URL = URL;
window.URLSearchParams = URLSearchParams;
window.Blob = Blob;

window.MutationObserver = function(cb) {
    return { observe: function() {}, disconnect: function() {}, takeRecords: function() { return []; } };
};
window.IntersectionObserver = function(cb) {
    return { observe: function() {}, unobserve: function() {}, disconnect: function() {} };
};
window.ResizeObserver = function(cb) {
    return { observe: function() {}, unobserve: function() {}, disconnect: function() {} };
};

window.CustomEvent = function(type, init) {
    init = init || {};
    this.type = type;
    this.detail = init.detail || null;
    this.bubbles = init.bubbles || false;
    this.cancelable = init.cancelable || false;
};
window.Event = function(type, init) {
    init = init || {};
    this.type = type;
    this.bubbles = init.bubbles || false;
    this.cancelable = init.cancelable || false;
    this.preventDefault = function() {};
    this.stopPropagation = function() {};
    this.stopImmediatePropagation = function() {};
};

// Give window EventTarget capabilities
window._listeners = {};
window.addEventListener    = EventTarget.prototype.addEventListener;
window.removeEventListener = EventTarget.prototype.removeEventListener;
window.dispatchEvent       = EventTarget.prototype.dispatchEvent;

// DOMContentLoaded / load already fired (document was parsed synchronously)
try { window.dispatchEvent(new window.Event('DOMContentLoaded')); } catch(e) {}
try { window.dispatchEvent(new window.Event('load')); } catch(e) {}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Legacy entry point (used by old skeleton)
# ─────────────────────────────────────────────────────────────────────────────


def build_host_globals(session: Session) -> dict[str, Any]:
    """
    Legacy helper — returns an empty dict since install_host_api() now
    handles everything directly on the ctx. Kept for API compatibility.
    """
    return {}
