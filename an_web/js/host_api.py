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

    def _py_get_children(node_id: str) -> str:
        """Return the direct children of a node as a JSON array."""
        el = _find_element_by_id(session, node_id)
        if el is None:
            return "[]"
        from an_web.dom.nodes import Element, TextNode
        result = []
        for child in getattr(el, "children", []):
            if isinstance(child, Element):
                result.append(marshal_element(child))
            elif isinstance(child, TextNode):
                result.append({
                    "nodeId": child.node_id,
                    "nodeType": 3,
                    "tag": "#text",
                    "tagName": "#text",
                    "id": "",
                    "className": "",
                    "attributes": {},
                    "textContent": child.data,
                    "children": [],
                })
        return json.dumps(result)

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
    ctx.add_callable("_py_get_children", _py_get_children)
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

    # ── document.cookie ───────────────────────────────────────────────────────

    def _py_get_cookies() -> str:
        """Return cookie string for current URL."""
        cookies = getattr(session, "cookies", None)
        url = getattr(session, "_current_url", "about:blank")
        if cookies is None:
            return ""
        return cookies.cookie_header(url) or ""

    def _py_set_cookie(cookie_str: str) -> None:
        """Set a cookie from JS (document.cookie = ...)."""
        cookies = getattr(session, "cookies", None)
        url = getattr(session, "_current_url", "about:blank")
        if cookies is None:
            return
        from urllib.parse import urlparse

        from an_web.net.cookies import Cookie
        parts = [p.strip() for p in cookie_str.split(";")]
        if not parts or not parts[0]:
            return
        name, _, value = parts[0].partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            return
        domain = urlparse(url).hostname or ""
        cookie = Cookie(name=name, value=value, domain=domain)
        for part in parts[1:]:
            key, _, val = part.partition("=")
            key = key.strip().lower()
            if key == "path":
                cookie.path = val.strip() or "/"
            elif key == "domain":
                cookie.domain = val.strip().lstrip(".")
        cookies.set(cookie)

    ctx.add_callable("_py_get_cookies", _py_get_cookies)
    ctx.add_callable("_py_set_cookie", _py_set_cookie)

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
        Supports both sync (loop not running) and async (enqueue for later) modes.
        """
        import asyncio
        network = getattr(session, "network", None)
        if network is None:
            return json.dumps({"ok": False, "status": 0, "text": "", "error": "no_network"})
        try:
            headers = json.loads(headers_json) if headers_json else {}
            body = json.loads(body_json) if body_json else None

            # Add browser-like headers for fetch requests
            if "Referer" not in headers:
                headers["Referer"] = getattr(session, "_current_url", "") or ""
            if "Sec-Fetch-Dest" not in headers:
                headers["Sec-Fetch-Dest"] = "empty"
            if "Sec-Fetch-Mode" not in headers:
                headers["Sec-Fetch-Mode"] = "cors"

            # Try to run synchronously
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                # In async context: use thread to avoid blocking
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        _do_fetch(network, url, method, headers, body)
                    )
                    try:
                        result = future.result(timeout=10.0)
                        return result
                    except Exception as exc:
                        log.debug("_py_fetch_sync thread error: %s", exc)
                        return json.dumps({"ok": False, "status": 0, "text": "",
                                          "error": str(exc)})
            else:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        _do_fetch(network, url, method, headers, body)
                    )
                    return result
                finally:
                    loop.close()

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

    # ── DOM Mutation callbacks ────────────────────────────────────────────────
    # These callbacks allow JS to actually modify the Python DOM tree.

    _node_id_counter = [1000000]  # Start high to avoid collision with parser IDs

    def _py_create_element(tag: str) -> str:
        """Create a new Element in the Python DOM and return its JSON."""
        from an_web.dom.nodes import Element
        _node_id_counter[0] += 1
        node_id = f"js_{_node_id_counter[0]}"
        el = Element(node_id=node_id, tag=tag.lower(), attributes={})
        # Store in a registry on the session for later lookup
        if not hasattr(session, "_js_created_nodes"):
            session._js_created_nodes = {}
        session._js_created_nodes[node_id] = el
        return json.dumps(marshal_element(el))

    def _py_create_text_node(text: str) -> str:
        """Create a TextNode in the Python DOM."""
        from an_web.dom.nodes import TextNode
        _node_id_counter[0] += 1
        node_id = f"js_{_node_id_counter[0]}"
        tn = TextNode(node_id=node_id, data=text)
        if not hasattr(session, "_js_created_nodes"):
            session._js_created_nodes = {}
        session._js_created_nodes[node_id] = tn
        return json.dumps({"nodeId": node_id, "nodeType": 3, "data": text,
                          "textContent": text, "tag": "#text"})

    def _py_create_document_fragment() -> str:
        """Create a DocumentFragment node backed by Python."""
        from an_web.dom.nodes import Element
        _node_id_counter[0] += 1
        node_id = f"js_{_node_id_counter[0]}"
        # Use Element as backing container — fragments act as element collections
        frag = Element(node_id=node_id, tag="#document-fragment", attributes={})
        if not hasattr(session, "_js_created_nodes"):
            session._js_created_nodes = {}
        session._js_created_nodes[node_id] = frag
        return json.dumps({"nodeId": node_id, "nodeType": 11, "tag": "",
                          "tagName": "", "attributes": {}, "children": []})

    def _py_append_child(parent_id: str, child_id: str) -> bool:
        """Append child node to parent in the Python DOM tree."""
        parent = _find_node(session, parent_id)
        child = _find_node(session, child_id)
        if parent is None or child is None:
            return False
        # Remove from old parent if already in tree
        if child.parent is not None:
            try:
                child.parent.children.remove(child)
            except ValueError:
                pass
        parent.append_child(child)
        # Register in document's id map
        from an_web.dom.nodes import Element
        doc = getattr(session, "_current_document", None)
        if doc and isinstance(child, Element):
            doc.register_element(child)
        return True

    def _py_remove_child(parent_id: str, child_id: str) -> bool:
        """Remove child node from parent in the Python DOM tree."""
        parent = _find_node(session, parent_id)
        child = _find_node(session, child_id)
        if parent is None or child is None:
            return False
        try:
            parent.remove_child(child)
            return True
        except (ValueError, Exception):
            return False

    def _py_insert_before(parent_id: str, new_id: str, ref_id: str) -> bool:
        """Insert new_node before ref_node under parent."""
        parent = _find_node(session, parent_id)
        new_node = _find_node(session, new_id)
        if parent is None or new_node is None:
            return False
        if not ref_id or ref_id == "null":
            parent.append_child(new_node)
            return True
        ref_node = _find_node(session, ref_id)
        if ref_node is None:
            parent.append_child(new_node)
            return True
        # Remove from old parent
        if new_node.parent is not None:
            try:
                new_node.parent.children.remove(new_node)
            except ValueError:
                pass
        # Insert at the correct position
        try:
            idx = parent.children.index(ref_node)
            new_node.parent = parent
            parent.children.insert(idx, new_node)
        except ValueError:
            parent.append_child(new_node)
        return True

    def _py_set_inner_html(node_id: str, html_str: str) -> bool:
        """Parse HTML string and replace node's children."""
        from an_web.dom.nodes import Element
        target = _find_node(session, node_id)
        if target is None:
            return False
        # Clear existing children
        target.children.clear()
        # Parse the HTML fragment
        if not html_str.strip():
            return True
        try:
            from an_web.browser.parser import parse_html
            doc = getattr(session, "_current_document", None)
            base_url = getattr(session, "_current_url", "about:blank")
            frag_doc = parse_html(f"<div>{html_str}</div>", base_url=base_url)
            # Find the wrapper div and steal its children
            for node in frag_doc.iter_descendants():
                if isinstance(node, Element) and node.tag == "div":
                    for child in list(node.children):
                        child.parent = target
                        target.children.append(child)
                        if isinstance(child, Element) and doc:
                            doc.register_element(child)
                            # Register all descendants too
                            for desc in child.iter_descendants():
                                if isinstance(desc, Element):
                                    doc.register_element(desc)
                    break
        except Exception as exc:
            log.debug("_py_set_inner_html error: %s", exc)
            return False
        return True

    def _py_set_text_content(node_id: str, text: str) -> bool:
        """Replace node's children with a single text node."""
        from an_web.dom.nodes import TextNode
        target = _find_node(session, node_id)
        if target is None:
            return False
        target.children.clear()
        if text:
            _node_id_counter[0] += 1
            tn = TextNode(node_id=f"js_{_node_id_counter[0]}", data=text)
            target.append_child(tn)
        return True

    def _py_insert_adjacent_html(node_id: str, position: str, html_str: str) -> bool:
        """Insert HTML relative to a node (beforebegin/afterbegin/beforeend/afterend)."""
        from an_web.dom.nodes import Element
        target = _find_node(session, node_id)
        if target is None or not html_str.strip():
            return False
        try:
            from an_web.browser.parser import parse_html
            base_url = getattr(session, "_current_url", "about:blank")
            frag_doc = parse_html(f"<div>{html_str}</div>", base_url=base_url)
            doc = getattr(session, "_current_document", None)
            new_nodes = []
            for node in frag_doc.iter_descendants():
                if isinstance(node, Element) and node.tag == "div":
                    new_nodes = list(node.children)
                    break

            pos = position.lower()
            parent = target.parent
            if pos == "beforeend":
                for n in new_nodes:
                    target.append_child(n)
                    _register_deep(n, doc)
            elif pos == "afterbegin":
                for i, n in enumerate(new_nodes):
                    n.parent = target
                    target.children.insert(i, n)
                    _register_deep(n, doc)
            elif pos == "beforebegin" and parent:
                idx = parent.children.index(target)
                for i, n in enumerate(new_nodes):
                    n.parent = parent
                    parent.children.insert(idx + i, n)
                    _register_deep(n, doc)
            elif pos == "afterend" and parent:
                idx = parent.children.index(target) + 1
                for i, n in enumerate(new_nodes):
                    n.parent = parent
                    parent.children.insert(idx + i, n)
                    _register_deep(n, doc)
        except Exception as exc:
            log.debug("_py_insert_adjacent_html error: %s", exc)
            return False
        return True

    def _py_clone_node(node_id: str, deep: bool) -> str:
        """Clone a node (optionally deep) and return its JSON."""
        from an_web.dom.nodes import Element
        source = _find_node(session, node_id)
        if source is None:
            return "null"
        clone = _deep_clone_node(source, deep, _node_id_counter, session)
        if clone is None:
            return "null"
        return json.dumps(marshal_element(clone) if isinstance(clone, Element)
                         else {"nodeId": clone.node_id, "nodeType": 3,
                               "data": getattr(clone, "data", ""),
                               "textContent": getattr(clone, "data", ""),
                               "tag": "#text"})

    def _py_remove_node(node_id: str) -> bool:
        """Remove a node from the tree."""
        target = _find_node(session, node_id)
        if target is None or target.parent is None:
            return False
        try:
            target.parent.children.remove(target)
            target.parent = None
            return True
        except ValueError:
            return False

    ctx.add_callable("_py_create_element", _py_create_element)
    ctx.add_callable("_py_create_text_node", _py_create_text_node)
    ctx.add_callable("_py_create_document_fragment", _py_create_document_fragment)
    ctx.add_callable("_py_append_child", _py_append_child)
    ctx.add_callable("_py_remove_child", _py_remove_child)
    ctx.add_callable("_py_insert_before", _py_insert_before)
    ctx.add_callable("_py_set_inner_html", _py_set_inner_html)
    ctx.add_callable("_py_set_text_content", _py_set_text_content)
    ctx.add_callable("_py_insert_adjacent_html", _py_insert_adjacent_html)
    ctx.add_callable("_py_clone_node", _py_clone_node)
    ctx.add_callable("_py_remove_node", _py_remove_node)

    # ── Async fetch bridge ────────────────────────────────────────────────────
    # Store pending fetch requests that will be resolved by the event loop

    def _py_fetch_async(request_id: str, url: str, method: str, body_json: str, headers_json: str) -> None:
        """Queue an async fetch request to be resolved by the event loop."""
        if not hasattr(session, "_pending_fetches"):
            session._pending_fetches = {}
        session._pending_fetches[request_id] = {
            "url": url,
            "method": method,
            "body_json": body_json,
            "headers_json": headers_json,
            "resolved": False,
            "result": None,
        }

    def _py_fetch_poll(request_id: str) -> str:
        """Check if an async fetch has been resolved."""
        fetches = getattr(session, "_pending_fetches", {})
        entry = fetches.get(request_id)
        if entry is None:
            return json.dumps({"resolved": False, "error": "not_found"})
        if entry.get("resolved"):
            return json.dumps({"resolved": True, "result": entry["result"]})
        return json.dumps({"resolved": False})

    ctx.add_callable("_py_fetch_async", _py_fetch_async)
    ctx.add_callable("_py_fetch_poll", _py_fetch_poll)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _find_element_by_id(session: Any, node_id: str) -> Any:
    """Locate a DOM Element by its internal node_id."""
    # Check JS-created nodes first (not yet in tree)
    js_nodes = getattr(session, "_js_created_nodes", {})
    if node_id in js_nodes:
        from an_web.dom.nodes import Element
        node = js_nodes[node_id]
        if isinstance(node, Element):
            return node
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return None
    from an_web.dom.nodes import Element
    for node in doc.iter_descendants():
        if isinstance(node, Element) and node.node_id == node_id:
            return node
    return None


def _find_node(session: Any, node_id: str) -> Any:
    """Locate any DOM Node (Element or TextNode) by node_id."""
    if not node_id or node_id == "null" or node_id == "__document__":
        doc = getattr(session, "_current_document", None)
        if node_id == "__document__":
            return doc
        return None
    # Check JS-created nodes first
    js_nodes = getattr(session, "_js_created_nodes", {})
    if node_id in js_nodes:
        return js_nodes[node_id]
    doc = getattr(session, "_current_document", None)
    if doc is None:
        return None
    for node in doc.iter_descendants():
        if getattr(node, "node_id", None) == node_id:
            return node
    return None


def _register_deep(node: Any, doc: Any) -> None:
    """Register a node and all descendants in the document's id map."""
    from an_web.dom.nodes import Element
    if doc and isinstance(node, Element):
        doc.register_element(node)
        for desc in node.iter_descendants():
            if isinstance(desc, Element):
                doc.register_element(desc)


def _deep_clone_node(node: Any, deep: bool, counter: list[int], session: Any) -> Any:
    """Deep or shallow clone a DOM node."""
    from an_web.dom.nodes import Element, TextNode
    counter[0] += 1
    new_id = f"js_{counter[0]}"

    if isinstance(node, TextNode):
        clone = TextNode(node_id=new_id, data=node.data)
        if not hasattr(session, "_js_created_nodes"):
            session._js_created_nodes = {}
        session._js_created_nodes[new_id] = clone
        return clone
    elif isinstance(node, Element):
        clone = Element(node_id=new_id, tag=node.tag,
                       attributes=dict(node.attributes))
        if not hasattr(session, "_js_created_nodes"):
            session._js_created_nodes = {}
        session._js_created_nodes[new_id] = clone
        if deep:
            for child in node.children:
                child_clone = _deep_clone_node(child, True, counter, session)
                if child_clone:
                    clone.append_child(child_clone)
        return clone
    return None


def _get_storage(session: Any, store_name: str) -> dict[str, str]:
    """Return the appropriate storage dict from the session."""
    attr = "_local_storage" if store_name == "local" else "_session_storage"
    if not hasattr(session, attr):
        setattr(session, attr, {})
    return getattr(session, attr)


async def _do_fetch(network: Any, url: str, method: str,
                    headers: dict, body: Any) -> str:
    """Perform an actual HTTP fetch and return JSON result string."""
    import httpx

    # Create a fresh client for thread-based fetch to avoid sharing connections
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=10.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
        },
    ) as client:
        try:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                resp = await client.post(url, json=body, headers=headers)
            else:
                resp = await client.request(method.upper(), url, headers=headers,
                                           content=json.dumps(body).encode() if body else None)

            return json.dumps({
                "ok": 200 <= resp.status_code < 400,
                "status": resp.status_code,
                "text": resp.text,
                "headers": dict(resp.headers),
                "url": str(resp.url),
            })
        except Exception as exc:
            return json.dumps({"ok": False, "status": 0, "text": "", "error": str(exc)})


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

var _elementIndex = 0;

function _makeElement(data) {
    if (!data) return null;
    var proto = (typeof HTMLElement !== 'undefined') ? HTMLElement.prototype : EventTarget.prototype;
    if (data.nodeType === 3 && typeof Text !== 'undefined') proto = Text.prototype;
    if (data.nodeType === 8 && typeof Comment !== 'undefined') proto = Comment.prototype;
    var el = Object.create(proto);
    el._listeners = {};
    el.nodeId       = data.nodeId || '';
    el.nodeType     = data.nodeType !== undefined ? data.nodeType : 1;
    el.tagName      = (data.tagName || data.tag || '').toUpperCase();
    el.localName    = (data.tag || data.tagName || '').toLowerCase();
    el.id           = data.id || '';
    el.className    = data.className || '';
    el._textContent = data.textContent || '';
    el._innerHTML   = data.innerHTML || '';
    el._attributes  = data.attributes || {};
    el._children    = (data.children || []).map(_makeElement);
    el.ownerDocument = (typeof document !== 'undefined') ? document : null;
    el.sourceIndex  = _elementIndex++;
    el.nodeName     = el.tagName || (el.nodeType === 3 ? '#text' : '');
    el.nodeValue    = el.nodeType === 3 ? el._textContent : null;

    // textContent — getter reads from Python, setter writes back
    Object.defineProperty(el, 'textContent', {
        get: function() {
            if (el.nodeId && el.nodeId.indexOf('_new_') < 0) {
                var tc = _py_get_text_content(el.nodeId);
                return tc || el._textContent;
            }
            return el._textContent;
        },
        set: function(v) {
            el._textContent = String(v);
            if (el.nodeId) _py_set_text_content(el.nodeId, String(v));
        },
        configurable: true
    });

    // innerHTML — getter reads from Python, setter parses HTML and updates DOM
    Object.defineProperty(el, 'innerHTML', {
        get: function() {
            if (el.nodeId && el.nodeId.indexOf('_new_') < 0) {
                return _py_get_inner_html(el.nodeId) || el._innerHTML;
            }
            return el._innerHTML;
        },
        set: function(v) {
            el._innerHTML = String(v);
            if (el.nodeId) {
                _py_set_inner_html(el.nodeId, String(v));
                el._childrenDirty = true;
            }
        },
        configurable: true
    });

    // outerHTML getter
    Object.defineProperty(el, 'outerHTML', {
        get: function() {
            var attrs = '';
            for (var k in el._attributes) {
                attrs += ' ' + k + '="' + el._attributes[k] + '"';
            }
            return '<' + el.localName + attrs + '>' + el.innerHTML + '</' + el.localName + '>';
        },
        configurable: true
    });

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

    // URL-derived properties for <a> and <area> elements (protocol, host, etc.)
    // Many libraries use document.createElement('a') as a URL parser.
    (function() {
        function _parseURL(href) {
            if (!href) return {protocol:'',host:'',hostname:'',port:'',pathname:'/',search:'',hash:''};
            var m = /^([a-z][a-z0-9+.-]*:)?\/\/([^/?#]*)?(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(href);
            if (!m) {
                // Relative or malformed — just set pathname
                var qIdx = href.indexOf('?'), hIdx = href.indexOf('#');
                var p = href, s = '', h = '';
                if (qIdx >= 0) { p = href.substring(0, qIdx); s = href.substring(qIdx); }
                if (hIdx >= 0) { h = href.substring(hIdx); if (qIdx < 0 || hIdx < qIdx) p = href.substring(0, hIdx); s = ''; }
                return {protocol:'',host:'',hostname:'',port:'',pathname:p||'/',search:s,hash:h};
            }
            var proto = m[1] || '';
            var fullHost = m[2] || '';
            var portSep = fullHost.lastIndexOf(':');
            var hostname = portSep >= 0 ? fullHost.substring(0, portSep) : fullHost;
            var port = portSep >= 0 ? fullHost.substring(portSep + 1) : '';
            return {
                protocol: proto,
                host: fullHost,
                hostname: hostname,
                port: port,
                pathname: m[3] || '/',
                search: m[4] || '',
                hash: m[5] || ''
            };
        }
        var urlProps = ['protocol','host','hostname','port','pathname','search','hash'];
        for (var i = 0; i < urlProps.length; i++) {
            (function(prop) {
                Object.defineProperty(el, prop, {
                    get: function() { return _parseURL(el._attributes['href'] || '')[prop]; },
                    set: function(v) {
                        var parts = _parseURL(el._attributes['href'] || '');
                        parts[prop] = v;
                        var url = parts.protocol + '//' + parts.host + parts.pathname + parts.search + parts.hash;
                        el.setAttribute('href', url);
                    },
                    configurable: true
                });
            })(urlProps[i]);
        }
    })();

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

    // children array access — dynamically fetched from Python DOM
    // _children is used as a cache; refreshed from Python when needed
    el._childrenDirty = true;
    function _refreshChildren() {
        if (el._childrenDirty && el.nodeId && el.nodeId.indexOf('_new_') < 0 && el.nodeId.indexOf('_frag_') < 0) {
            var raw = _safeParse(_py_get_children(el.nodeId));
            if (raw && raw.length !== undefined) {
                el._children = raw.map(_makeElement);
                el._childrenDirty = false;
            }
        }
        return el._children;
    }
    Object.defineProperty(el, 'children', {
        get: function() { return _refreshChildren(); },
        configurable: true
    });
    Object.defineProperty(el, 'childNodes', {
        get: function() { return _refreshChildren(); },
        configurable: true
    });
    Object.defineProperty(el, 'firstChild', {
        get: function() { var ch = _refreshChildren(); return ch[0] || null; },
        configurable: true
    });
    Object.defineProperty(el, 'lastChild', {
        get: function() { var ch = _refreshChildren(); return ch[ch.length - 1] || null; },
        configurable: true
    });
    Object.defineProperty(el, 'firstElementChild', {
        get: function() {
            var ch = _refreshChildren();
            for (var i = 0; i < ch.length; i++) {
                if (ch[i] && ch[i].nodeType === 1) return ch[i];
            }
            return null;
        },
        configurable: true
    });
    Object.defineProperty(el, 'lastElementChild', {
        get: function() {
            var ch = _refreshChildren();
            for (var i = ch.length - 1; i >= 0; i--) {
                if (ch[i] && ch[i].nodeType === 1) return ch[i];
            }
            return null;
        },
        configurable: true
    });
    Object.defineProperty(el, 'childElementCount', {
        get: function() {
            var ch = _refreshChildren();
            var count = 0;
            for (var i = 0; i < ch.length; i++) {
                if (ch[i] && ch[i].nodeType === 1) count++;
            }
            return count;
        },
        configurable: true
    });
    Object.defineProperty(el, 'hasChildNodes', {
        value: function() { return _refreshChildren().length > 0; },
        configurable: true
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

    // DOM mutation — backed by Python callbacks to modify the real DOM tree
    el.appendChild  = function(child) {
        if (child && child.nodeId && el.nodeId) {
            _py_append_child(el.nodeId, child.nodeId);
            el._childrenDirty = true;
        }
        return child;
    };
    el.removeChild  = function(child) {
        if (child && child.nodeId && el.nodeId) {
            _py_remove_child(el.nodeId, child.nodeId);
            el._childrenDirty = true;
        }
        return child;
    };
    el.insertBefore = function(newNode, ref) {
        if (newNode && newNode.nodeId && el.nodeId) {
            var refId = (ref && ref.nodeId) ? ref.nodeId : 'null';
            _py_insert_before(el.nodeId, newNode.nodeId, refId);
            el._childrenDirty = true;
        }
        return newNode;
    };
    el.replaceChild = function(newNode, old) {
        if (old && newNode && el.nodeId) {
            _py_remove_child(el.nodeId, old.nodeId);
            _py_append_child(el.nodeId, newNode.nodeId);
            el._childrenDirty = true;
        }
        return old;
    };
    el.cloneNode    = function(deep) {
        var raw = _safeParse(_py_clone_node(el.nodeId, !!deep));
        return raw ? _makeElement(raw) : _makeElement(data);
    };
    el.remove       = function() {
        _py_remove_node(el.nodeId);
    };
    el.before       = function() {
        var parent = el.parentElement;
        if (!parent) return;
        for (var i = 0; i < arguments.length; i++) {
            var node = arguments[i];
            if (typeof node === 'string') {
                node = document.createTextNode(node);
            }
            parent.insertBefore(node, el);
        }
    };
    el.after        = function() {
        var parent = el.parentElement;
        if (!parent) return;
        var next = el.nextElementSibling;
        for (var i = 0; i < arguments.length; i++) {
            var node = arguments[i];
            if (typeof node === 'string') {
                node = document.createTextNode(node);
            }
            if (next) parent.insertBefore(node, next);
            else parent.appendChild(node);
        }
    };
    el.prepend      = function() {
        var first = el._children[0] || null;
        for (var i = arguments.length - 1; i >= 0; i--) {
            var node = arguments[i];
            if (typeof node === 'string') {
                node = document.createTextNode(node);
            }
            el.insertBefore(node, first);
        }
    };
    el.append       = function() {
        for (var i = 0; i < arguments.length; i++) {
            var node = arguments[i];
            if (typeof node === 'string') {
                node = document.createTextNode(node);
            }
            el.appendChild(node);
        }
    };
    el.replaceWith  = function() {
        var parent = el.parentElement;
        if (!parent) return;
        for (var i = 0; i < arguments.length; i++) {
            var node = arguments[i];
            if (typeof node === 'string') {
                node = document.createTextNode(node);
            }
            parent.insertBefore(node, el);
        }
        el.remove();
    };

    el.insertAdjacentHTML = function(pos, html) {
        if (el.nodeId && html) {
            _py_insert_adjacent_html(el.nodeId, pos, html);
            el._childrenDirty = true;
        }
    };
    el.insertAdjacentText = function(pos, text) {
        el.insertAdjacentHTML(pos, text);
    };
    el.insertAdjacentElement = function(pos, el2) {
        if (el2) {
            var parent = el.parentElement;
            if (pos === 'beforebegin' && parent) parent.insertBefore(el2, el);
            else if (pos === 'afterbegin') el.prepend(el2);
            else if (pos === 'beforeend') el.appendChild(el2);
            else if (pos === 'afterend' && parent) {
                var next = el.nextElementSibling;
                if (next) parent.insertBefore(el2, next);
                else parent.appendChild(el2);
            }
        }
        return el2;
    };

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

    // compareDocumentPosition — required by Sizzle/jQuery
    el.compareDocumentPosition = function(other) {
        if (!other) return 1; // DISCONNECTED
        if (el === other) return 0;
        // Use sourceIndex for ordering when available
        if (typeof other.sourceIndex === 'number') {
            if (el.sourceIndex < other.sourceIndex) return 4; // FOLLOWING
            if (el.sourceIndex > other.sourceIndex) return 2; // PRECEDING
        }
        // Check containment
        if (el.contains && el.contains(other)) return 20; // CONTAINED_BY | FOLLOWING
        if (other.contains && other.contains(el)) return 10; // CONTAINS | PRECEDING
        return 4; // FOLLOWING (default)
    };

    return el;
}

// ── document ─────────────────────────────────────────────────────────────────

var document = (function() {
    var doc = Object.create(EventTarget.prototype);
    doc._listeners = {};
    doc.nodeType = 9;
    doc.nodeName = '#document';
    doc.readyState = 'complete';
    doc.compatMode = 'CSS1Compat';
    doc.characterSet = 'UTF-8';
    doc.charset = 'UTF-8';
    doc.inputEncoding = 'UTF-8';
    doc.contentType = 'text/html';
    doc.defaultView = window;
    doc.ownerDocument = null;
    doc.parentNode = null;
    doc.parentElement = null;
    doc.implementation = {
        createDocument: function() { return doc; },
        createHTMLDocument: function(title) { return doc; },
        createDocumentType: function(name, publicId, systemId) {
            var dt = new DocumentType();
            dt.name = name;
            dt.publicId = publicId || '';
            dt.systemId = systemId || '';
            return dt;
        },
        hasFeature: function() { return true; }
    };
    // DOCTYPE node
    doc.doctype = (function() {
        var dt = Object.create(DocumentType.prototype);
        dt.nodeType = 10;
        dt.name = 'html';
        dt.publicId = '';
        dt.systemId = '';
        dt.nodeName = 'html';
        return dt;
    })();

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
        get: function() { return _py_get_cookies(); },
        set: function(v) { _py_set_cookie(String(v)); },
    });
    Object.defineProperty(doc, 'body', {
        get: function() { return doc.querySelector('body'); },
    });
    Object.defineProperty(doc, 'head', {
        get: function() { return doc.querySelector('head'); },
    });
    Object.defineProperty(doc, 'documentElement', {
        get: function() { return doc.querySelector('html'); },
    });
    Object.defineProperty(doc, 'childNodes', {
        get: function() {
            var de = doc.documentElement;
            return de ? [doc.doctype, de] : [doc.doctype];
        },
    });
    Object.defineProperty(doc, 'children', {
        get: function() {
            var de = doc.documentElement;
            return de ? [de] : [];
        },
    });
    Object.defineProperty(doc, 'firstChild', {
        get: function() { return doc.doctype || doc.documentElement; },
    });
    Object.defineProperty(doc, 'lastChild', {
        get: function() { return doc.documentElement || doc.doctype; },
    });
    Object.defineProperty(doc, 'firstElementChild', {
        get: function() { return doc.documentElement; },
    });
    Object.defineProperty(doc, 'lastElementChild', {
        get: function() { return doc.documentElement; },
    });
    Object.defineProperty(doc, 'childElementCount', {
        get: function() { return doc.documentElement ? 1 : 0; },
    });
    doc.hasChildNodes = function() { return true; };
    doc.compareDocumentPosition = function(other) {
        if (!other) return 1;
        if (other === doc) return 0;
        return 20; // CONTAINED_BY | FOLLOWING
    };
    doc.contains = function(node) { return node !== null && node !== undefined; };

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

    // createElement — creates a real node in the Python DOM
    doc.createElement = function(tag) {
        var raw = _safeParse(_py_create_element(tag));
        if (raw) return _makeElement(raw);
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
        var raw = _safeParse(_py_create_text_node(text));
        if (raw) {
            var tn = _makeElement(raw);
            tn.nodeType = 3;
            tn.data = text;
            tn.nodeValue = text;
            return tn;
        }
        return { nodeType: 3, nodeId: '', data: text, textContent: text, nodeValue: text };
    };
    doc.createDocumentFragment = function() {
        var raw = _safeParse(_py_create_document_fragment());
        if (raw) return _makeElement(raw);
        return _makeElement({ nodeId: '_frag_' + Date.now(), tag: '', tagName: '', nodeType: 11, attributes: {}, children: [] });
    };

    // Event helpers
    doc.createEvent = function(type) {
        return { type: '', bubbles: false, cancelable: false, initEvent: function(t) { this.type = t; } };
    };

    // Write — appends parsed HTML to body for basic document.write support
    doc.write = function(html) {
        if (html && doc.body) {
            doc.body.innerHTML = doc.body.innerHTML + html;
        }
    };
    doc.writeln = function(html) { doc.write((html || '') + '\n'); };
    doc.open = function() {};
    doc.close = function() {};
    doc.createRange = function() { return new Range(); };
    doc.createTreeWalker = function(root, whatToShow, filter) {
        var tw = new TreeWalker();
        tw.root = root;
        tw.currentNode = root;
        return tw;
    };
    doc.createNodeIterator = function(root, whatToShow, filter) {
        var ni = new NodeIterator();
        ni.root = root;
        return ni;
    };
    doc.adoptNode = function(node) { return node; };
    doc.importNode = function(node, deep) { return node; };
    // Node filter constants
    doc.ELEMENT_NODE = 1;
    doc.TEXT_NODE = 3;
    doc.DOCUMENT_NODE = 9;

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
    h.pushState    = function(state, title, url) {};
    h.replaceState = function(state, title, url) {};
    h.back         = function() {};
    h.forward      = function() {};
    h.go           = function() {};
    return h;
})();

// ── navigator ─────────────────────────────────────────────────────────────────

var navigator = {
    userAgent:   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    platform:    'Win32',
    language:    'ko-KR',
    languages:   ['ko-KR', 'ko', 'en-US', 'en'],
    onLine:      true,
    cookieEnabled: true,
    doNotTrack:  null,
    hardwareConcurrency: 8,
    maxTouchPoints: 0,
    vendor: 'Google Inc.',
    appName: 'Netscape',
    appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    product: 'Gecko',
    productSub: '20030107',
    vendorSub: '',
    webdriver: false,
    geolocation: null,
    clipboard: null,
    permissions: { query: function() { return Promise.resolve({ state: 'denied' }); } },
    mediaDevices: { enumerateDevices: function() { return Promise.resolve([]); } },
    serviceWorker: { register: function() { return Promise.reject(new Error('not supported')); } },
    sendBeacon: function() { return true; },
    connection: { effectiveType: '4g', downlink: 10, rtt: 50, saveData: false },
};

// ── screen ────────────────────────────────────────────────────────────────────

var screen = {
    width: 1920, height: 1080,
    availWidth: 1920, availHeight: 1040,
    colorDepth: 24, pixelDepth: 24,
    orientation: { type: 'landscape-primary', angle: 0 },
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
var _timerIdToKey = {};

function setTimeout(fn, delay) {
    if (typeof delay !== 'number') delay = 0;
    var key = '_t' + Date.now() + '_' + Math.random();
    _timerCallbacks[key] = fn;
    var id = _py_set_timeout_ms(delay, key);
    _timerIdToKey[id] = key;
    return id;
}
function clearTimeout(id) {
    var key = _timerIdToKey[id];
    if (key) {
        delete _timerCallbacks[key];
        delete _timerIdToKey[id];
    }
    _py_clear_timeout(id);
}
function setInterval(fn, delay) {
    if (typeof delay !== 'number' || delay < 1) delay = 1;
    var key = '_i' + Date.now() + '_' + Math.random();
    _timerCallbacks[key] = fn;
    var id = _py_set_timeout_ms(delay, key);
    _timerIdToKey[id] = key;
    _intervalCallbacks[id] = { fn: fn, delay: delay, key: key };
    return id;
}
function clearInterval(id) {
    delete _intervalCallbacks[id];
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
    var firedStr = _py_get_fired_timers();
    var fired = JSON.parse(firedStr);
    for (var i = 0; i < fired.length; i++) {
        var tid = fired[i];
        var key = _timerIdToKey[tid];
        if (key && _timerCallbacks[key]) {
            try {
                _timerCallbacks[key]();
            } catch(e) {
                console.error('Timer callback error:', e);
            }
            // Check if it's an interval — re-register
            if (_intervalCallbacks[tid]) {
                var interval = _intervalCallbacks[tid];
                var newId = _py_set_timeout_ms(interval.delay, interval.key);
                _timerIdToKey[newId] = interval.key;
                _intervalCallbacks[newId] = interval;
                delete _intervalCallbacks[tid];
            }
            delete _timerCallbacks[key];
            delete _timerIdToKey[tid];
        }
    }
    return fired.length;
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
window.self = window;
window.top  = window;
window.parent = window;
window.frameElement = null;
window.frames = [];
window.length = 0;

// window needs EventTarget capabilities
window._listeners = {};
window.addEventListener = EventTarget.prototype.addEventListener.bind(window);
window.removeEventListener = EventTarget.prototype.removeEventListener.bind(window);
window.dispatchEvent = EventTarget.prototype.dispatchEvent.bind(window);

// Standard event handler properties (null by default, like real browsers)
var _eventHandlerNames = [
    'onabort', 'onblur', 'onchange', 'onclick', 'onclose', 'oncontextmenu',
    'ondblclick', 'onerror', 'onfocus', 'onfocusin', 'onfocusout', 'oninput',
    'oninvalid', 'onkeydown', 'onkeypress', 'onkeyup', 'onload', 'onmousedown',
    'onmouseenter', 'onmouseleave', 'onmousemove', 'onmouseout', 'onmouseover',
    'onmouseup', 'onreset', 'onresize', 'onscroll', 'onselect', 'onsubmit',
    'onunload', 'onbeforeunload', 'onhashchange', 'onmessage', 'onoffline',
    'ononline', 'onpagehide', 'onpageshow', 'onpopstate', 'onstorage',
    'ontouchstart', 'ontouchmove', 'ontouchend', 'ontouchcancel',
    'onanimationend', 'onanimationiteration', 'onanimationstart',
    'ontransitionend', 'onwheel', 'ondrag', 'ondragend', 'ondragenter',
    'ondragleave', 'ondragover', 'ondragstart', 'ondrop', 'onpointerdown',
    'onpointermove', 'onpointerup', 'onpointercancel', 'onpointerover',
    'onpointerout', 'onpointerenter', 'onpointerleave', 'ongotpointercapture',
    'onlostpointercapture', 'oncut', 'oncopy', 'onpaste', 'onbeforecut',
    'onbeforecopy', 'onbeforepaste', 'onselectstart', 'onreadystatechange',
];
for (var _ehi = 0; _ehi < _eventHandlerNames.length; _ehi++) {
    if (!(_eventHandlerNames[_ehi] in window)) {
        window[_eventHandlerNames[_ehi]] = null;
    }
}
// Also set on HTMLElement prototype so created elements have them too
for (var _ehi2 = 0; _ehi2 < _eventHandlerNames.length; _ehi2++) {
    if (!(_eventHandlerNames[_ehi2] in HTMLElement.prototype)) {
        HTMLElement.prototype[_eventHandlerNames[_ehi2]] = null;
    }
}

// ── DOM constructor stubs (for instanceof / polyfill checks) ──────────────────
function Node() {}
Node.ELEMENT_NODE = 1;
Node.ATTRIBUTE_NODE = 2;
Node.TEXT_NODE = 3;
Node.CDATA_SECTION_NODE = 4;
Node.COMMENT_NODE = 8;
Node.DOCUMENT_NODE = 9;
Node.DOCUMENT_TYPE_NODE = 10;
Node.DOCUMENT_FRAGMENT_NODE = 11;
Node.prototype = Object.create(EventTarget.prototype);
Node.prototype.constructor = Node;
Node.prototype.nodeType = 1;
Node.prototype.nodeName = '';
Node.prototype.nodeValue = null;
Node.prototype.parentNode = null;
Node.prototype.childNodes = [];
Node.prototype.firstChild = null;
Node.prototype.lastChild = null;
Node.prototype.previousSibling = null;
Node.prototype.nextSibling = null;
Node.prototype.ownerDocument = null;
Node.prototype.contains = function(other) {
    if (this === other) return true;
    var ch = this.children || this.childNodes || [];
    for (var i = 0; i < ch.length; i++) {
        if (ch[i] && ch[i].contains && ch[i].contains(other)) return true;
    }
    return false;
};
Node.prototype.compareDocumentPosition = function(other) { return 0; };
Node.prototype.isEqualNode = function(other) { return this === other; };
Node.prototype.isSameNode = function(other) { return this === other; };
Node.prototype.cloneNode = function(deep) { return this; };
Node.prototype.normalize = function() {};
Node.prototype.hasChildNodes = function() { return (this.children || this.childNodes || []).length > 0; };
Node.prototype.getRootNode = function() { return document; };

function Element() {}
Element.prototype = Object.create(Node.prototype);
Element.prototype.constructor = Element;
Element.prototype.nodeType = 1;
Element.prototype.matches = function(sel) { return false; };
Element.prototype.closest = function(sel) { return null; };
Element.prototype.getBoundingClientRect = function() {
    return { top:0, right:0, bottom:0, left:0, width:0, height:0, x:0, y:0 };
};
Element.prototype.getClientRects = function() { return []; };
Element.prototype.setAttribute = function(n,v) { this._attributes = this._attributes || {}; this._attributes[n] = String(v); };
Element.prototype.getAttribute = function(n) { return (this._attributes || {})[n] || null; };
Element.prototype.removeAttribute = function(n) { if (this._attributes) delete this._attributes[n]; };
Element.prototype.hasAttribute = function(n) { return !!(this._attributes && n in this._attributes); };
Element.prototype.getAttributeNames = function() { return Object.keys(this._attributes || {}); };
Element.prototype.getElementsByTagName = function(tag) {
    tag = tag.toUpperCase();
    var result = [];
    var ch = this.children || [];
    for (var i = 0; i < ch.length; i++) {
        if (ch[i] && ch[i].tagName === tag) result.push(ch[i]);
        if (ch[i] && ch[i].getElementsByTagName) {
            result = result.concat(ch[i].getElementsByTagName(tag));
        }
    }
    return result;
};
Element.prototype.getElementsByClassName = function(cls) { return []; };
Element.prototype.insertAdjacentElement = function(pos, el) { return el; };
Element.prototype.insertAdjacentText = function(pos, text) {};
Element.prototype.scrollIntoView = function() {};
Element.prototype.focus = function() {};
Element.prototype.blur = function() {};
Element.prototype.click = function() {};
// classList, dataset, style are defined per-element in _makeElement()
// — no prototype definitions here to avoid setter conflicts.

function HTMLElement() {}
HTMLElement.prototype = Object.create(Element.prototype);
HTMLElement.prototype.constructor = HTMLElement;
HTMLElement.prototype.offsetWidth = 0;
HTMLElement.prototype.offsetHeight = 0;
HTMLElement.prototype.offsetTop = 0;
HTMLElement.prototype.offsetLeft = 0;
HTMLElement.prototype.offsetParent = null;
HTMLElement.prototype.clientWidth = 0;
HTMLElement.prototype.clientHeight = 0;
HTMLElement.prototype.clientTop = 0;
HTMLElement.prototype.clientLeft = 0;
HTMLElement.prototype.scrollWidth = 0;
HTMLElement.prototype.scrollHeight = 0;
HTMLElement.prototype.scrollTop = 0;
HTMLElement.prototype.scrollLeft = 0;

function Text() {}
Text.prototype = Object.create(Node.prototype);
Text.prototype.constructor = Text;
Text.prototype.nodeType = 3;

function Comment() {}
Comment.prototype = Object.create(Node.prototype);
Comment.prototype.constructor = Comment;
Comment.prototype.nodeType = 8;

function DocumentFragment() {}
DocumentFragment.prototype = Object.create(Node.prototype);
DocumentFragment.prototype.constructor = DocumentFragment;
DocumentFragment.prototype.nodeType = 11;
DocumentFragment.prototype.children = [];
DocumentFragment.prototype.querySelector = function() { return null; };
DocumentFragment.prototype.querySelectorAll = function() { return []; };
DocumentFragment.prototype.getElementById = function() { return null; };
DocumentFragment.prototype.appendChild = function(c) { this.children = this.children || []; this.children.push(c); return c; };

function HTMLDocument() {}
HTMLDocument.prototype = Object.create(Node.prototype);
HTMLDocument.prototype.constructor = HTMLDocument;

// HTML element subclasses commonly checked by polyfills
function HTMLDivElement() {}
HTMLDivElement.prototype = Object.create(HTMLElement.prototype);
function HTMLSpanElement() {}
HTMLSpanElement.prototype = Object.create(HTMLElement.prototype);
function HTMLAnchorElement() {}
HTMLAnchorElement.prototype = Object.create(HTMLElement.prototype);
function HTMLImageElement() {}
HTMLImageElement.prototype = Object.create(HTMLElement.prototype);
function HTMLInputElement() {}
HTMLInputElement.prototype = Object.create(HTMLElement.prototype);
function HTMLButtonElement() {}
HTMLButtonElement.prototype = Object.create(HTMLElement.prototype);
function HTMLFormElement() {}
HTMLFormElement.prototype = Object.create(HTMLElement.prototype);
function HTMLScriptElement() {}
HTMLScriptElement.prototype = Object.create(HTMLElement.prototype);
function HTMLStyleElement() {}
HTMLStyleElement.prototype = Object.create(HTMLElement.prototype);
function HTMLLinkElement() {}
HTMLLinkElement.prototype = Object.create(HTMLElement.prototype);
function HTMLIFrameElement() {}
HTMLIFrameElement.prototype = Object.create(HTMLElement.prototype);
function HTMLCanvasElement() {}
HTMLCanvasElement.prototype = Object.create(HTMLElement.prototype);
HTMLCanvasElement.prototype.getContext = function() { return null; };
function HTMLVideoElement() {}
HTMLVideoElement.prototype = Object.create(HTMLElement.prototype);
function HTMLAudioElement() {}
HTMLAudioElement.prototype = Object.create(HTMLElement.prototype);
function HTMLTemplateElement() {}
HTMLTemplateElement.prototype = Object.create(HTMLElement.prototype);
HTMLTemplateElement.prototype.content = new DocumentFragment();

// SVG stubs
function SVGElement() {}
SVGElement.prototype = Object.create(Element.prototype);

// Additional DOM types needed by polyfills
function DocumentType() {}
DocumentType.prototype = Object.create(Node.prototype);
DocumentType.prototype.constructor = DocumentType;
DocumentType.prototype.nodeType = 10;

function ProcessingInstruction() {}
ProcessingInstruction.prototype = Object.create(Node.prototype);
ProcessingInstruction.prototype.constructor = ProcessingInstruction;

function CDATASection() {}
CDATASection.prototype = Object.create(Text.prototype);
CDATASection.prototype.constructor = CDATASection;
CDATASection.prototype.nodeType = 4;

function Range() {}
Range.prototype.cloneContents = function() { return new DocumentFragment(); };
Range.prototype.cloneRange = function() { return new Range(); };
Range.prototype.collapse = function() {};
Range.prototype.createContextualFragment = function(html) { return new DocumentFragment(); };
Range.prototype.deleteContents = function() {};
Range.prototype.detach = function() {};
Range.prototype.getBoundingClientRect = function() { return {top:0,right:0,bottom:0,left:0,width:0,height:0}; };
Range.prototype.getClientRects = function() { return []; };
Range.prototype.insertNode = function() {};
Range.prototype.selectNode = function() {};
Range.prototype.selectNodeContents = function() {};
Range.prototype.setEnd = function() {};
Range.prototype.setEndAfter = function() {};
Range.prototype.setEndBefore = function() {};
Range.prototype.setStart = function() {};
Range.prototype.setStartAfter = function() {};
Range.prototype.setStartBefore = function() {};
Range.prototype.surroundContents = function() {};
Range.prototype.toString = function() { return ''; };

function Selection() {}
Selection.prototype.addRange = function() {};
Selection.prototype.collapse = function() {};
Selection.prototype.collapseToEnd = function() {};
Selection.prototype.collapseToStart = function() {};
Selection.prototype.containsNode = function() { return false; };
Selection.prototype.deleteFromDocument = function() {};
Selection.prototype.extend = function() {};
Selection.prototype.getRangeAt = function() { return new Range(); };
Selection.prototype.removeAllRanges = function() {};
Selection.prototype.removeRange = function() {};
Selection.prototype.selectAllChildren = function() {};
Selection.prototype.setBaseAndExtent = function() {};
Selection.prototype.toString = function() { return ''; };
Selection.prototype.rangeCount = 0;
Selection.prototype.anchorNode = null;
Selection.prototype.focusNode = null;
Selection.prototype.isCollapsed = true;
Selection.prototype.type = 'None';

function TreeWalker() {}
TreeWalker.prototype.currentNode = null;
TreeWalker.prototype.firstChild = function() { return null; };
TreeWalker.prototype.lastChild = function() { return null; };
TreeWalker.prototype.nextNode = function() { return null; };
TreeWalker.prototype.nextSibling = function() { return null; };
TreeWalker.prototype.parentNode = function() { return null; };
TreeWalker.prototype.previousNode = function() { return null; };
TreeWalker.prototype.previousSibling = function() { return null; };

function NodeIterator() {}
NodeIterator.prototype.nextNode = function() { return null; };
NodeIterator.prototype.previousNode = function() { return null; };
NodeIterator.prototype.detach = function() {};

// Expose all constructors on window
window.Node = Node;
window.Element = Element;
window.HTMLElement = HTMLElement;
window.HTMLDocument = HTMLDocument;
window.Text = Text;
window.Comment = Comment;
window.DocumentFragment = DocumentFragment;
window.DocumentType = DocumentType;
window.ProcessingInstruction = ProcessingInstruction;
window.CDATASection = CDATASection;
window.Range = Range;
window.Selection = Selection;
window.TreeWalker = TreeWalker;
window.NodeIterator = NodeIterator;
window.HTMLDivElement = HTMLDivElement;
window.HTMLSpanElement = HTMLSpanElement;
window.HTMLAnchorElement = HTMLAnchorElement;
window.HTMLImageElement = HTMLImageElement;
window.HTMLInputElement = HTMLInputElement;
window.HTMLButtonElement = HTMLButtonElement;
window.HTMLFormElement = HTMLFormElement;
window.HTMLScriptElement = HTMLScriptElement;
window.HTMLStyleElement = HTMLStyleElement;
window.HTMLLinkElement = HTMLLinkElement;
window.HTMLIFrameElement = HTMLIFrameElement;
window.HTMLCanvasElement = HTMLCanvasElement;
window.HTMLVideoElement = HTMLVideoElement;
window.HTMLAudioElement = HTMLAudioElement;
window.HTMLTemplateElement = HTMLTemplateElement;
window.SVGElement = SVGElement;
window.CharacterData = Text;  // polyfill compat
window.NodeList = Array;      // polyfill compat

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
    return new Proxy({}, {
        get: function(target, prop) {
            if (prop === 'getPropertyValue') return function(p) { return ''; };
            if (prop === 'display') return 'block';
            if (prop === 'visibility') return 'visible';
            if (prop === 'position') return 'static';
            if (prop === 'overflow') return 'visible';
            if (prop === 'opacity') return '1';
            if (prop === 'width') return 'auto';
            if (prop === 'height') return 'auto';
            if (prop === 'fontSize') return '16px';
            if (prop === 'color') return 'rgb(0, 0, 0)';
            if (prop === 'backgroundColor') return 'rgba(0, 0, 0, 0)';
            if (prop === 'length') return 0;
            if (prop === 'cssText') return '';
            return '';
        }
    });
};
window.matchMedia = function(query) {
    var matches = false;
    if (query.indexOf('prefers-color-scheme: light') >= 0) matches = true;
    if (query.indexOf('(min-width:') >= 0) {
        var m = query.match(/min-width:\s*(\d+)/);
        if (m && parseInt(m[1]) <= 1920) matches = true;
    }
    return {
        matches: matches,
        media: query,
        addListener: function(){},
        removeListener: function(){},
        addEventListener: function(){},
        removeEventListener: function(){},
        onchange: null,
        dispatchEvent: function() { return true; }
    };
};
window.getSelection = function() { return new Selection(); };

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
window.PerformanceObserver = function(cb) {
    return { observe: function() {}, disconnect: function() {}, takeRecords: function() { return []; } };
};
PerformanceObserver.supportedEntryTypes = [];

window.CustomEvent = function(type, init) {
    init = init || {};
    this.type = type;
    this.detail = init.detail || null;
    this.bubbles = init.bubbles || false;
    this.cancelable = init.cancelable || false;
    this.target = null;
    this.currentTarget = null;
    this.eventPhase = 0;
    this.timeStamp = _py_perf_now();
    this.preventDefault = function() { this.defaultPrevented = true; };
    this.stopPropagation = function() {};
    this.stopImmediatePropagation = function() {};
    this.defaultPrevented = false;
};
window.Event = function(type, init) {
    init = init || {};
    this.type = type;
    this.bubbles = init.bubbles || false;
    this.cancelable = init.cancelable || false;
    this.target = null;
    this.currentTarget = null;
    this.eventPhase = 0;
    this.timeStamp = _py_perf_now();
    this.preventDefault = function() { this.defaultPrevented = true; };
    this.stopPropagation = function() {};
    this.stopImmediatePropagation = function() {};
    this.defaultPrevented = false;
};
window.MouseEvent = function(type, init) {
    window.Event.call(this, type, init);
    init = init || {};
    this.clientX = init.clientX || 0;
    this.clientY = init.clientY || 0;
    this.button = init.button || 0;
};
window.KeyboardEvent = function(type, init) {
    window.Event.call(this, type, init);
    init = init || {};
    this.key = init.key || '';
    this.code = init.code || '';
    this.keyCode = init.keyCode || 0;
};
window.FocusEvent = function(type, init) { window.Event.call(this, type, init); };
window.ErrorEvent = function(type, init) {
    window.Event.call(this, type, init);
    init = init || {};
    this.message = init.message || '';
    this.filename = init.filename || '';
    this.lineno = init.lineno || 0;
    this.colno = init.colno || 0;
    this.error = init.error || null;
};
window.InputEvent = function(type, init) {
    window.Event.call(this, type, init);
    this.data = (init || {}).data || null;
    this.inputType = (init || {}).inputType || '';
};

// DOMParser for runtime HTML parsing
window.DOMParser = function() {};
window.DOMParser.prototype.parseFromString = function(str, type) {
    return document;
};

// TextEncoder/TextDecoder
window.TextEncoder = function() {};
window.TextEncoder.prototype.encode = function(str) {
    var arr = [];
    for (var i = 0; i < str.length; i++) arr.push(str.charCodeAt(i));
    return new Uint8Array(arr);
};
window.TextDecoder = function() {};
window.TextDecoder.prototype.decode = function(buf) { return String.fromCharCode.apply(null, buf); };

// window dimensions
window.innerWidth = 1920;
window.innerHeight = 1080;
window.outerWidth = 1920;
window.outerHeight = 1080;
window.devicePixelRatio = 1;
window.scrollX = 0;
window.scrollY = 0;
window.pageXOffset = 0;
window.pageYOffset = 0;
window.scrollTo = function() {};
window.scrollBy = function() {};
window.scroll = function() {};

// atob/btoa
window.atob = function(s) {
    // Simple base64 decode
    var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
    var o = '';
    for (var i = 0; i < s.length;) {
        var a = chars.indexOf(s.charAt(i++));
        var b = chars.indexOf(s.charAt(i++));
        var c = chars.indexOf(s.charAt(i++));
        var d = chars.indexOf(s.charAt(i++));
        var bits = (a << 18) | (b << 12) | (c << 6) | d;
        o += String.fromCharCode((bits >> 16) & 0xFF);
        if (c !== 64) o += String.fromCharCode((bits >> 8) & 0xFF);
        if (d !== 64) o += String.fromCharCode(bits & 0xFF);
    }
    return o;
};
window.btoa = function(s) {
    var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    var o = '';
    for (var i = 0; i < s.length; i += 3) {
        var a = s.charCodeAt(i);
        var b = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
        var c = i + 2 < s.length ? s.charCodeAt(i + 2) : 0;
        o += chars[(a >> 2) & 63];
        o += chars[((a & 3) << 4) | ((b >> 4) & 15)];
        o += (i + 1 < s.length) ? chars[((b & 15) << 2) | ((c >> 6) & 3)] : '=';
        o += (i + 2 < s.length) ? chars[c & 63] : '=';
    }
    return o;
};

// Map, Set, WeakMap, WeakSet - already in QuickJS but check
if (typeof Map === 'undefined') { window.Map = function() { this._data = {}; }; }
if (typeof Set === 'undefined') { window.Set = function() { this._data = []; }; }
if (typeof WeakMap === 'undefined') { window.WeakMap = function() {}; }
if (typeof WeakRef === 'undefined') { window.WeakRef = function(t) { this.deref = function() { return t; }; }; }
if (typeof Symbol === 'undefined') { window.Symbol = function(d) { return '__sym_' + (d || '') + '_' + Date.now(); }; }
if (typeof Proxy === 'undefined') { window.Proxy = function(t, h) { return t; }; }

// crypto.getRandomValues stub
window.crypto = {
    getRandomValues: function(arr) {
        for (var i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
        return arr;
    },
    randomUUID: function() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    },
    subtle: {}
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
