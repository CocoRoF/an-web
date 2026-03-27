"""
Host Web API implementations injected into V8 context (PyMiniRacer).

Architecture (V8 mode)
----------------------
PyMiniRacer does not support registering Python callables into JS
(no ``add_callable``).  Instead, all ``_py_*`` host functions are
implemented as pure JavaScript operating on pre-injected DOM state:

1. **State injection**: Python serialises the entire DOM tree and
   session state (URL, cookies, storage) into JSON and injects it
   into V8 as global variables.
2. **JS bridge functions**: All ``_py_*`` functions are defined in
   JavaScript, operating on the injected state.
3. **JS browser shim**: The bootstrap script constructs document,
   window, location, etc. using these bridge functions.
4. **Mutation sync**: After script execution, Python reads a mutation
   log from V8 and applies DOM changes back to the Python tree.

Injection order:
    1. Serialise and inject DOM tree + session state
    2. Define all ``_py_*`` bridge functions in JS
    3. Run the JS bootstrap that builds the browser globals
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
    Install the complete host Web API into a V8 context (PyMiniRacer).

    This is called once after MiniRacer() creation.  It:
    1. Serialises the DOM tree and session state into JS globals.
    2. Defines all ``_py_*`` bridge functions as pure JS.
    3. Evaluates the JS bootstrap that builds the browser globals.

    Args:
        ctx:     A ``py_mini_racer.MiniRacer`` instance.
        session: The owning Session (for DOM/network/storage access).
    """
    _inject_dom_state(ctx, session)
    _inject_session_state(ctx, session)
    ctx.eval(_JS_PY_FUNCTIONS)
    ctx.eval(_JS_BOOTSTRAP)

# ─────────────────────────────────────────────────────────────────────────────
# State injection (Python → V8)
# ─────────────────────────────────────────────────────────────────────────────


def _inject_dom_state(ctx: Any, session: Session) -> None:
    """Serialise the full Python DOM tree and inject it into V8."""
    dom_json = _serialize_dom_tree(session)
    ctx.eval(f"var _domTree = {dom_json};")


def _inject_session_state(ctx: Any, session: Session) -> None:
    """Inject URL, cookies, storage, and other session state into V8."""
    state_json = _serialize_session_state(session)
    ctx.eval(f"var _sessionState = {state_json};")


def reinject_dom_state(ctx: Any, session: Session) -> None:
    """Re-inject DOM tree and session state after the document has changed.

    Called between HTML parsing and script execution so that V8 scripts
    see the real DOM tree rather than the empty one from context creation.
    """
    _inject_dom_state(ctx, session)
    _inject_session_state(ctx, session)


def _serialize_dom_tree(session: Session) -> str:
    """Walk the Python DOM and return a JSON string for V8."""
    from an_web.dom.nodes import Element, TextNode

    doc = getattr(session, "_current_document", None)
    if doc is None:
        return json.dumps(
            {"nodes": {}, "rootId": None, "idIndex": {}, "tagIndex": {}}
        )

    nodes: dict[str, Any] = {}
    id_index: dict[str, str] = {}
    tag_index: dict[str, list[str]] = {}
    root_id: str | None = None

    for node in doc.iter_descendants():
        if isinstance(node, Element):
            nid = node.node_id
            child_ids = [
                getattr(c, "node_id", "") for c in node.children
            ]
            parent_id = (
                getattr(node.parent, "node_id", None)
                if node.parent else None
            )
            attrs = dict(node.attributes)
            nodes[nid] = {
                "nodeId": nid,
                "nodeType": 1,
                "tag": node.tag,
                "tagName": node.tag.upper(),
                "attributes": attrs,
                "id": attrs.get("id", ""),
                "className": attrs.get("class", ""),
                "textContent": node.text_content or "",
                "children": child_ids,
                "parentId": parent_id,
                "isInteractive": getattr(node, "is_interactive", False),
                "visibilityState": getattr(
                    node, "visibility_state", "visible"
                ),
                "semanticRole": getattr(node, "semantic_role", None),
                "stableSelector": getattr(node, "stable_selector", None),
            }
            if node.tag == "html":
                root_id = nid
            eid = attrs.get("id", "")
            if eid:
                id_index[eid] = nid
            tag_index.setdefault(node.tag, []).append(nid)

        elif isinstance(node, TextNode):
            nid = node.node_id
            parent_id = (
                getattr(node.parent, "node_id", None)
                if node.parent else None
            )
            nodes[nid] = {
                "nodeId": nid,
                "nodeType": 3,
                "tag": "#text",
                "data": node.data,
                "textContent": node.data,
                "parentId": parent_id,
            }

    # Also include any JS-created nodes from a previous context
    js_nodes = getattr(session, "_js_created_nodes", {})
    for nid, node in js_nodes.items():
        if nid not in nodes:
            if isinstance(node, Element):
                nodes[nid] = {
                    "nodeId": nid, "nodeType": 1, "tag": node.tag,
                    "tagName": node.tag.upper(),
                    "attributes": dict(node.attributes),
                    "id": node.attributes.get("id", ""),
                    "className": node.attributes.get("class", ""),
                    "textContent": node.text_content or "",
                    "children": [
                        getattr(c, "node_id", "") for c in node.children
                    ],
                    "parentId": (
                        getattr(node.parent, "node_id", None)
                        if node.parent else None
                    ),
                }
            elif isinstance(node, TextNode):
                nodes[nid] = {
                    "nodeId": nid, "nodeType": 3, "tag": "#text",
                    "data": node.data, "textContent": node.data,
                    "parentId": (
                        getattr(node.parent, "node_id", None)
                        if node.parent else None
                    ),
                }

    return json.dumps(
        {
            "nodes": nodes,
            "rootId": root_id,
            "idIndex": id_index,
            "tagIndex": tag_index,
        },
        default=str,
    )


def _serialize_session_state(session: Session) -> str:
    """Serialise URL, cookies, storage etc. for V8 injection."""
    url = getattr(session, "_current_url", "about:blank") or "about:blank"
    doc = getattr(session, "_current_document", None)
    title = (getattr(doc, "title", "") or "") if doc else ""

    cookies = ""
    cookie_jar = getattr(session, "cookies", None)
    if cookie_jar:
        cookies = cookie_jar.cookie_header(url) or ""

    local_storage = dict(getattr(session, "_local_storage", {}))
    session_storage = dict(getattr(session, "_session_storage", {}))
    history_length = len(getattr(session, "_history", []))

    return json.dumps(
        {
            "url": url,
            "title": title,
            "cookies": cookies,
            "localStorage": local_storage,
            "sessionStorage": session_storage,
            "historyLength": history_length,
        },
        default=str,
    )


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


def _maybe_queue_dynamic_script(session: Any, node: Any) -> None:
    """Queue a dynamically inserted <script> element for async loading."""
    from an_web.dom.nodes import Element
    if not isinstance(node, Element) or node.tag != "script":
        return
    src = node.get_attribute("src")
    if not src:
        return
    # Check type attribute — only queue executable JS
    stype = (node.get_attribute("type") or "").lower()
    if stype and stype not in ("text/javascript", "application/javascript", "module", ""):
        return
    if not hasattr(session, "_pending_dynamic_scripts"):
        session._pending_dynamic_scripts = []
    # Avoid duplicates
    if src not in [s["src"] for s in session._pending_dynamic_scripts]:
        session._pending_dynamic_scripts.append({"src": src, "node_id": node.node_id})


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


_JS_PY_FUNCTIONS = r"""
'use strict';

// ═══════════════════════════════════════════════════════════════════════════
// V8 Bridge Layer — Pure JS implementations of all _py_* host functions
// ═══════════════════════════════════════════════════════════════════════════
//
// In the original architecture these were Python callables registered via
// ctx.add_callable().  PyMiniRacer (V8) does not support that, so every
// _py_* function is now a JS function operating on pre-injected state:
//
//   _domTree       — full DOM tree serialised from Python
//   _sessionState  — URL, cookies, storage, history length
//
// Mutations are tracked in _mutationLog and synced back to Python after
// each eval() round.

var _mutationLog   = [];
var _bridgeCommands = [];
var _nodeIdCounter  = 2000000;   // high start to avoid collision

// ── Console ──────────────────────────────────────────────────────────────

var _consoleMessages = [];

function _py_console_log()  { /* no-op in V8 — output captured by _consoleMessages */ }
function _py_console_warn() {}
function _py_console_error(){}

// ── Document metadata ────────────────────────────────────────────────────

function _py_doc_meta() {
    return JSON.stringify({
        title:      _sessionState.title || '',
        url:        _sessionState.url   || 'about:blank',
        readyState: 'complete',
        nodeType:   9,
    });
}

function _py_doc_get_title()  { return _sessionState.title || ''; }
function _py_doc_set_title(t) { _sessionState.title = String(t); _mutationLog.push({type:'setTitle', title:String(t)}); }
function _py_doc_get_url()    { return _sessionState.url || 'about:blank'; }

// ── CSS selector engine (minimal) ───────────────────────────────────────

function _parseCompoundSelector(sel) {
    var res = {tag:'', id:'', classes:[], attrs:[]};
    var i = 0;
    if (i < sel.length && /[a-zA-Z*]/.test(sel[i])) {
        var e = i;
        while (e < sel.length && /[a-zA-Z0-9-]/.test(sel[e])) e++;
        res.tag = sel.substring(i, e).toLowerCase();
        i = e;
    }
    while (i < sel.length) {
        if (sel[i] === '#') {
            i++; var e = i;
            while (e < sel.length && /[a-zA-Z0-9_-]/.test(sel[e])) e++;
            res.id = sel.substring(i, e); i = e;
        } else if (sel[i] === '.') {
            i++; var e = i;
            while (e < sel.length && /[a-zA-Z0-9_-]/.test(sel[e])) e++;
            res.classes.push(sel.substring(i, e)); i = e;
        } else if (sel[i] === '[') {
            i++; var e = sel.indexOf(']', i); if (e < 0) break;
            var inner = sel.substring(i, e);
            var m = inner.match(/^([a-zA-Z_:][a-zA-Z0-9_:.\-]*)\s*(~=|\^=|\$=|\*=|=)\s*["']?([^"']*)["']?$/);
            if (m) res.attrs.push({name:m[1], op:m[2], value:m[3]});
            else   res.attrs.push({name:inner.trim(), op:'exists'});
            i = e + 1;
        } else if (sel[i] === ':') {
            // skip pseudo-selectors
            break;
        } else { i++; }
    }
    return res;
}

function _matchesSimple(nodeId, compound) {
    var n = _domTree.nodes[nodeId];
    if (!n || n.nodeType !== 1) return false;
    var p = (typeof compound === 'string') ? _parseCompoundSelector(compound) : compound;
    if (p.tag && p.tag !== '*' && n.tag !== p.tag) return false;
    if (p.id  && (n.id || n.attributes && n.attributes.id || '') !== p.id) return false;
    var cls = ' ' + (n.className || (n.attributes && n.attributes['class']) || '') + ' ';
    for (var i = 0; i < p.classes.length; i++) {
        if (cls.indexOf(' ' + p.classes[i] + ' ') < 0) return false;
    }
    for (var i = 0; i < p.attrs.length; i++) {
        var a = p.attrs[i], attrs = n.attributes || {};
        if (a.op === 'exists') { if (!(a.name in attrs)) return false; }
        else if (a.op === '=')  { if (attrs[a.name] !== a.value) return false; }
        else if (a.op === '~=') { if ((' '+(attrs[a.name]||'')+' ').indexOf(' '+a.value+' ')<0) return false; }
        else if (a.op === '^=') { if (!(attrs[a.name]||'').startsWith(a.value)) return false; }
        else if (a.op === '$=') { if (!(attrs[a.name]||'').endsWith(a.value)) return false; }
        else if (a.op === '*=') { if ((attrs[a.name]||'').indexOf(a.value)<0) return false; }
    }
    return true;
}

function _isDescendantOf(nid, ancestorId) {
    var node = _domTree.nodes[nid];
    while (node && node.parentId) {
        if (node.parentId === ancestorId) return true;
        node = _domTree.nodes[node.parentId];
    }
    return false;
}

function _tokenizeSelector(sel) {
    var tokens = [], cur = '', i = 0;
    while (i < sel.length) {
        var ch = sel[i];
        if (ch === ' ' || ch === '>' || ch === '+' || ch === '~') {
            if (cur.trim()) tokens.push({sel: cur.trim()});
            var comb = ' ';
            while (i < sel.length && ' >+~'.indexOf(sel[i]) >= 0) {
                if (sel[i] !== ' ') comb = sel[i];
                i++;
            }
            if (tokens.length > 0) tokens.push({comb: comb});
            cur = '';
        } else { cur += ch; i++; }
    }
    if (cur.trim()) tokens.push({sel: cur.trim()});
    return tokens;
}

function _walkDescendants(rootId, fn) {
    var node = _domTree.nodes[rootId];
    if (!node || !node.children) return;
    for (var i = 0; i < node.children.length; i++) {
        fn(node.children[i]);
        _walkDescendants(node.children[i], fn);
    }
}

function _cssSelectAll(rootId, selector) {
    if (!selector || !rootId) return [];
    // comma-separated
    if (selector.indexOf(',') >= 0) {
        var parts = selector.split(','), results = [], seen = {};
        for (var p = 0; p < parts.length; p++) {
            var matches = _cssSelectAll(rootId, parts[p].trim());
            for (var j = 0; j < matches.length; j++) {
                if (!seen[matches[j]]) { seen[matches[j]] = true; results.push(matches[j]); }
            }
        }
        return results;
    }
    selector = selector.trim();
    var tokens = _tokenizeSelector(selector);

    if (tokens.length === 1) {
        // Fast path: ID lookup
        var s = tokens[0].sel;
        if (s[0] === '#') {
            var nid = _domTree.idIndex[s.slice(1)];
            if (nid && _isDescendantOf(nid, rootId)) return [nid];
            return [];
        }
        // Simple walk
        var result = [];
        _walkDescendants(rootId, function(nid) {
            if (_matchesSimple(nid, s)) result.push(nid);
        });
        return result;
    }

    // Complex: tokenised
    var candidates = [];
    var first = tokens[0].sel;
    if (first[0] === '#') {
        var nid = _domTree.idIndex[first.slice(1)];
        if (nid && _isDescendantOf(nid, rootId)) candidates = [nid];
    } else {
        _walkDescendants(rootId, function(nid) {
            if (_matchesSimple(nid, first)) candidates.push(nid);
        });
    }

    for (var t = 1; t < tokens.length; t += 2) {
        if (t + 1 >= tokens.length) break;
        var comb = tokens[t].comb, next = tokens[t+1].sel;
        var newCands = [];
        for (var c = 0; c < candidates.length; c++) {
            var cid = candidates[c];
            if (comb === '>') {
                var cn = _domTree.nodes[cid];
                if (cn && cn.children) {
                    for (var k = 0; k < cn.children.length; k++) {
                        if (_matchesSimple(cn.children[k], next)) newCands.push(cn.children[k]);
                    }
                }
            } else {
                _walkDescendants(cid, function(nid) {
                    if (_matchesSimple(nid, next)) newCands.push(nid);
                });
            }
        }
        candidates = newCands;
    }
    return candidates;
}

function _cssSelectOne(rootId, selector) {
    var all = _cssSelectAll(rootId, selector);
    return all.length > 0 ? all[0] : null;
}

function _nodeToJson(nodeId) {
    var n = _domTree.nodes[nodeId];
    return n ? JSON.stringify(n) : 'null';
}

function _nodesToJson(nodeIds) {
    var arr = [];
    for (var i = 0; i < nodeIds.length; i++) {
        var n = _domTree.nodes[nodeIds[i]];
        if (n) arr.push(n);
    }
    return JSON.stringify(arr);
}

// ── Document queries ─────────────────────────────────────────────────────

function _py_query_selector(selector) {
    var nid = _cssSelectOne(_domTree.rootId, selector);
    return nid ? _nodeToJson(nid) : 'null';
}

function _py_query_selector_all(selector) {
    var nids = _cssSelectAll(_domTree.rootId, selector);
    return _nodesToJson(nids);
}

function _py_get_element_by_id(elemId) {
    var nid = _domTree.idIndex[elemId];
    return nid ? _nodeToJson(nid) : 'null';
}

function _py_get_elements_by_tag(tag) {
    tag = tag.toLowerCase();
    var nids = _domTree.tagIndex[tag] || [];
    return _nodesToJson(nids);
}

function _py_get_elements_by_class(className) {
    var result = [];
    _walkDescendants(_domTree.rootId, function(nid) {
        var n = _domTree.nodes[nid];
        if (n && n.nodeType === 1) {
            var cls = ' ' + (n.className || (n.attributes && n.attributes['class']) || '') + ' ';
            if (cls.indexOf(' ' + className + ' ') >= 0) result.push(nid);
        }
    });
    return _nodesToJson(result);
}

// ── Scoped queries ───────────────────────────────────────────────────────

function _py_query_selector_in(nodeId, selector) {
    var nid = _cssSelectOne(nodeId, selector);
    return nid ? _nodeToJson(nid) : 'null';
}

function _py_query_selector_all_in(nodeId, selector) {
    var nids = _cssSelectAll(nodeId, selector);
    return _nodesToJson(nids);
}

// ── Element attribute access ─────────────────────────────────────────────

function _py_get_attribute(nodeId, attrName) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.attributes) return 'null';
    var v = n.attributes[attrName];
    return v !== undefined ? JSON.stringify(v) : 'null';
}

function _py_set_attribute(nodeId, attrName, value) {
    var n = _domTree.nodes[nodeId];
    if (!n) return;
    if (!n.attributes) n.attributes = {};
    n.attributes[attrName] = value;
    if (attrName === 'id')    { n.id = value; _domTree.idIndex[value] = nodeId; }
    if (attrName === 'class') { n.className = value; }
    _mutationLog.push({type:'setAttribute', nodeId:nodeId, name:attrName, value:value});
}

function _py_remove_attribute(nodeId, attrName) {
    var n = _domTree.nodes[nodeId];
    if (n && n.attributes) {
        delete n.attributes[attrName];
        if (attrName === 'id')    n.id = '';
        if (attrName === 'class') n.className = '';
    }
    _mutationLog.push({type:'removeAttribute', nodeId:nodeId, name:attrName});
}

function _py_has_attribute(nodeId, attrName) {
    var n = _domTree.nodes[nodeId];
    return !!(n && n.attributes && attrName in n.attributes);
}

// ── Text / innerHTML ─────────────────────────────────────────────────────

function _computeTextContent(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n) return '';
    if (n.nodeType === 3) return n.data || '';
    var txt = '';
    if (n.children) {
        for (var i = 0; i < n.children.length; i++) txt += _computeTextContent(n.children[i]);
    }
    return txt;
}

function _py_get_text_content(nodeId) {
    return _computeTextContent(nodeId);
}

function _nodeToHtml(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n) return '';
    if (n.nodeType === 3) return n.data || '';
    var attrs = '';
    if (n.attributes) { for (var k in n.attributes) attrs += ' ' + k + '="' + n.attributes[k] + '"'; }
    var inner = '';
    if (n.children) { for (var i = 0; i < n.children.length; i++) inner += _nodeToHtml(n.children[i]); }
    return '<' + n.tag + attrs + '>' + inner + '</' + n.tag + '>';
}

function _py_get_inner_html(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.children) return '';
    var html = '';
    for (var i = 0; i < n.children.length; i++) html += _nodeToHtml(n.children[i]);
    return html;
}

function _py_set_text_content(nodeId, text) {
    var n = _domTree.nodes[nodeId];
    if (!n) return false;
    n.children = [];
    n.textContent = text;
    if (text) {
        _nodeIdCounter++;
        var tnId = 'js_' + _nodeIdCounter;
        var tn = {nodeId:tnId, nodeType:3, tag:'#text', data:text, textContent:text, parentId:nodeId};
        _domTree.nodes[tnId] = tn;
        n.children.push(tnId);
    }
    _mutationLog.push({type:'setTextContent', nodeId:nodeId, text:text});
    return true;
}

// ── Minimal HTML fragment parser ─────────────────────────────────────────

function _parseHtmlFragment(html) {
    var nodes = [], pos = 0;
    var voidTags = {br:1,hr:1,img:1,input:1,link:1,meta:1,area:1,base:1,col:1,embed:1,param:1,source:1,track:1,wbr:1};
    while (pos < html.length) {
        if (html[pos] === '<') {
            if (html[pos+1] === '/') { // skip close tags
                var gt = html.indexOf('>', pos);
                pos = gt >= 0 ? gt + 1 : html.length;
                continue;
            }
            if (html.substring(pos, pos+4) === '<!--') { // skip comments
                var endC = html.indexOf('-->', pos);
                pos = endC >= 0 ? endC + 3 : html.length;
                continue;
            }
            var gt = html.indexOf('>', pos);
            if (gt < 0) break;
            var tagContent = html.substring(pos+1, gt);
            var selfClose = tagContent[tagContent.length-1] === '/';
            if (selfClose) tagContent = tagContent.slice(0,-1);
            var spIdx = tagContent.search(/\s/);
            var tagName = (spIdx >= 0 ? tagContent.slice(0,spIdx) : tagContent).toLowerCase().trim();
            var attrStr = spIdx >= 0 ? tagContent.slice(spIdx) : '';
            if (!tagName || tagName[0] === '!' || tagName[0] === '?') { pos = gt+1; continue; }
            var attrs = {};
            var attrRe = /\s+([a-zA-Z_:][a-zA-Z0-9_:.\-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
            var am;
            while ((am = attrRe.exec(attrStr)) !== null) {
                attrs[am[1]] = am[2] !== undefined ? am[2] : (am[3] !== undefined ? am[3] : (am[4] || ''));
            }
            _nodeIdCounter++;
            var newNode = {
                nodeId:'js_'+_nodeIdCounter, nodeType:1, tag:tagName, tagName:tagName.toUpperCase(),
                attributes:attrs, id:attrs.id||'', className:attrs['class']||'',
                textContent:'', children:[], parentId:null
            };
            _domTree.nodes[newNode.nodeId] = newNode;
            if (newNode.id) _domTree.idIndex[newNode.id] = newNode.nodeId;
            if (!_domTree.tagIndex[tagName]) _domTree.tagIndex[tagName] = [];
            _domTree.tagIndex[tagName].push(newNode.nodeId);
            nodes.push(newNode);
            pos = gt + 1;
            if (selfClose || voidTags[tagName]) continue;
            // Find matching close tag
            var depth = 1, searchPos = pos;
            while (depth > 0 && searchPos < html.length) {
                var nextOpen = html.indexOf('<' + tagName, searchPos);
                var nextClose = html.indexOf('</' + tagName, searchPos);
                if (nextClose < 0) break;
                if (nextOpen >= 0 && nextOpen < nextClose) {
                    depth++;
                    searchPos = nextOpen + tagName.length + 1;
                } else {
                    depth--;
                    if (depth === 0) {
                        var innerH = html.substring(pos, nextClose);
                        var innerChildren = _parseHtmlFragment(innerH);
                        for (var j = 0; j < innerChildren.length; j++) {
                            innerChildren[j].parentId = newNode.nodeId;
                            newNode.children.push(innerChildren[j].nodeId);
                        }
                        pos = html.indexOf('>', nextClose) + 1;
                        if (pos === 0) pos = html.length;
                    } else {
                        searchPos = nextClose + tagName.length + 2;
                    }
                }
            }
        } else {
            var nextTag = html.indexOf('<', pos);
            var text = nextTag >= 0 ? html.substring(pos, nextTag) : html.substring(pos);
            if (text.trim()) {
                _nodeIdCounter++;
                var tn = {nodeId:'js_'+_nodeIdCounter, nodeType:3, tag:'#text',
                          data:text, textContent:text, parentId:null};
                _domTree.nodes[tn.nodeId] = tn;
                nodes.push(tn);
            }
            pos = nextTag >= 0 ? nextTag : html.length;
        }
    }
    return nodes;
}

function _py_set_inner_html(nodeId, html) {
    var n = _domTree.nodes[nodeId];
    if (!n) return false;
    n.children = [];
    if (html && html.trim()) {
        var newNodes = _parseHtmlFragment(html);
        for (var i = 0; i < newNodes.length; i++) {
            newNodes[i].parentId = nodeId;
            n.children.push(newNodes[i].nodeId);
        }
    }
    _mutationLog.push({type:'setInnerHTML', nodeId:nodeId, html:html||''});
    return true;
}

// ── Tree navigation ──────────────────────────────────────────────────────

function _py_get_parent(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.parentId) return 'null';
    return _nodeToJson(n.parentId);
}

function _py_get_siblings(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.parentId) return JSON.stringify({prev:null, next:null});
    var parent = _domTree.nodes[n.parentId];
    if (!parent || !parent.children) return JSON.stringify({prev:null, next:null});
    var idx = parent.children.indexOf(nodeId);
    var prev = null, next = null;
    for (var i = idx-1; i >= 0; i--) {
        var s = _domTree.nodes[parent.children[i]];
        if (s && s.nodeType === 1) { prev = s; break; }
    }
    for (var i = idx+1; i < parent.children.length; i++) {
        var s = _domTree.nodes[parent.children[i]];
        if (s && s.nodeType === 1) { next = s; break; }
    }
    return JSON.stringify({prev:prev, next:next});
}

function _py_get_children(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.children) return '[]';
    var result = [];
    for (var i = 0; i < n.children.length; i++) {
        var child = _domTree.nodes[n.children[i]];
        if (child) result.push(child);
    }
    return JSON.stringify(result);
}

// ── DOM collections ──────────────────────────────────────────────────────

function _py_get_forms()  { return _nodesToJson(_domTree.tagIndex['form'] || []); }
function _py_get_links()  {
    var tags = _domTree.tagIndex['a'] || [], result = [];
    for (var i = 0; i < tags.length; i++) {
        var n = _domTree.nodes[tags[i]];
        if (n && n.attributes && n.attributes.href !== undefined) result.push(tags[i]);
    }
    return _nodesToJson(result);
}
function _py_get_images() { return _nodesToJson(_domTree.tagIndex['img'] || []); }

// ── DOM mutation ─────────────────────────────────────────────────────────

function _py_create_element(tag) {
    _nodeIdCounter++;
    var nodeId = 'js_' + _nodeIdCounter;
    tag = tag.toLowerCase();
    var node = {
        nodeId:nodeId, nodeType:1, tag:tag, tagName:tag.toUpperCase(),
        attributes:{}, id:'', className:'', textContent:'', innerHTML:'',
        children:[], parentId:null, isInteractive:false, visibilityState:'visible',
        semanticRole:null, stableSelector:null
    };
    _domTree.nodes[nodeId] = node;
    if (!_domTree.tagIndex[tag]) _domTree.tagIndex[tag] = [];
    _domTree.tagIndex[tag].push(nodeId);
    _mutationLog.push({type:'createElement', nodeId:nodeId, tag:tag});
    return JSON.stringify(node);
}

function _py_create_text_node(text) {
    _nodeIdCounter++;
    var nodeId = 'js_' + _nodeIdCounter;
    var node = {nodeId:nodeId, nodeType:3, tag:'#text', data:text, textContent:text, parentId:null};
    _domTree.nodes[nodeId] = node;
    _mutationLog.push({type:'createTextNode', nodeId:nodeId, text:text});
    return JSON.stringify(node);
}

function _py_create_document_fragment() {
    _nodeIdCounter++;
    var nodeId = 'js_' + _nodeIdCounter;
    var node = {nodeId:nodeId, nodeType:11, tag:'#document-fragment', tagName:'',
                attributes:{}, children:[], parentId:null};
    _domTree.nodes[nodeId] = node;
    return JSON.stringify(node);
}

function _py_append_child(parentId, childId) {
    var parent = _domTree.nodes[parentId], child = _domTree.nodes[childId];
    if (!parent || !child) return false;
    // Remove from old parent
    if (child.parentId) {
        var old = _domTree.nodes[child.parentId];
        if (old && old.children) {
            var idx = old.children.indexOf(childId);
            if (idx >= 0) old.children.splice(idx, 1);
        }
    }
    if (!parent.children) parent.children = [];
    parent.children.push(childId);
    child.parentId = parentId;
    if (child.id) _domTree.idIndex[child.id] = childId;
    _mutationLog.push({type:'appendChild', parentId:parentId, childId:childId});
    // Queue dynamic script
    if (child.tag === 'script' && child.attributes && child.attributes.src) {
        _bridgeCommands.push({type:'dynamic_script', src:child.attributes.src});
    }
    return true;
}

function _py_remove_child(parentId, childId) {
    var parent = _domTree.nodes[parentId];
    if (!parent || !parent.children) return false;
    var idx = parent.children.indexOf(childId);
    if (idx >= 0) {
        parent.children.splice(idx, 1);
        var child = _domTree.nodes[childId];
        if (child) child.parentId = null;
        _mutationLog.push({type:'removeChild', parentId:parentId, childId:childId});
        return true;
    }
    return false;
}

function _py_insert_before(parentId, newId, refId) {
    var parent = _domTree.nodes[parentId], newNode = _domTree.nodes[newId];
    if (!parent || !newNode) return false;
    if (newNode.parentId) {
        var old = _domTree.nodes[newNode.parentId];
        if (old && old.children) {
            var idx = old.children.indexOf(newId);
            if (idx >= 0) old.children.splice(idx, 1);
        }
    }
    if (!parent.children) parent.children = [];
    if (!refId || refId === 'null') {
        parent.children.push(newId);
    } else {
        var ri = parent.children.indexOf(refId);
        if (ri >= 0) parent.children.splice(ri, 0, newId);
        else parent.children.push(newId);
    }
    newNode.parentId = parentId;
    if (newNode.id) _domTree.idIndex[newNode.id] = newId;
    _mutationLog.push({type:'insertBefore', parentId:parentId, newId:newId, refId:refId||'null'});
    if (newNode.tag === 'script' && newNode.attributes && newNode.attributes.src) {
        _bridgeCommands.push({type:'dynamic_script', src:newNode.attributes.src});
    }
    return true;
}

function _py_insert_adjacent_html(nodeId, position, html) {
    var n = _domTree.nodes[nodeId];
    if (!n || !html || !html.trim()) return false;
    var newNodes = _parseHtmlFragment(html);
    var pos = position.toLowerCase();
    var parent = n.parentId ? _domTree.nodes[n.parentId] : null;
    if (pos === 'beforeend') {
        if (!n.children) n.children = [];
        for (var i = 0; i < newNodes.length; i++) {
            newNodes[i].parentId = nodeId;
            n.children.push(newNodes[i].nodeId);
        }
    } else if (pos === 'afterbegin') {
        if (!n.children) n.children = [];
        for (var i = newNodes.length-1; i >= 0; i--) {
            newNodes[i].parentId = nodeId;
            n.children.unshift(newNodes[i].nodeId);
        }
    } else if (pos === 'beforebegin' && parent && parent.children) {
        var idx = parent.children.indexOf(nodeId);
        if (idx >= 0) {
            for (var i = 0; i < newNodes.length; i++) {
                newNodes[i].parentId = n.parentId;
                parent.children.splice(idx+i, 0, newNodes[i].nodeId);
            }
        }
    } else if (pos === 'afterend' && parent && parent.children) {
        var idx = parent.children.indexOf(nodeId) + 1;
        for (var i = 0; i < newNodes.length; i++) {
            newNodes[i].parentId = n.parentId;
            parent.children.splice(idx+i, 0, newNodes[i].nodeId);
        }
    }
    _mutationLog.push({type:'insertAdjacentHTML', nodeId:nodeId, position:position, html:html});
    return true;
}

function _py_clone_node(nodeId, deep) {
    function _clone(nid, doDeep) {
        var n = _domTree.nodes[nid];
        if (!n) return null;
        _nodeIdCounter++;
        var newId = 'js_' + _nodeIdCounter;
        var clone = JSON.parse(JSON.stringify(n));
        clone.nodeId = newId;
        clone.parentId = null;
        clone.children = [];
        _domTree.nodes[newId] = clone;
        if (doDeep && n.children) {
            for (var i = 0; i < n.children.length; i++) {
                var cc = _clone(n.children[i], true);
                if (cc) { cc.parentId = newId; clone.children.push(cc.nodeId); }
            }
        }
        return clone;
    }
    var clone = _clone(nodeId, !!deep);
    return clone ? JSON.stringify(clone) : 'null';
}

function _py_remove_node(nodeId) {
    var n = _domTree.nodes[nodeId];
    if (!n || !n.parentId) return false;
    var parent = _domTree.nodes[n.parentId];
    if (!parent || !parent.children) return false;
    var idx = parent.children.indexOf(nodeId);
    if (idx >= 0) {
        parent.children.splice(idx, 1);
        n.parentId = null;
        _mutationLog.push({type:'removeNode', nodeId:nodeId});
        return true;
    }
    return false;
}

// ── Window / navigation ──────────────────────────────────────────────────

function _py_win_href()       { return _sessionState.url || 'about:blank'; }
function _py_win_navigate(url){ _bridgeCommands.push({type:'navigate', url:url}); }
function _py_history_length() { return _sessionState.historyLength || 1; }

// ── Storage ──────────────────────────────────────────────────────────────

function _py_storage_get(store, key) {
    var s = store === 'local' ? _sessionState.localStorage : _sessionState.sessionStorage;
    return s[key] !== undefined ? JSON.stringify(s[key]) : 'null';
}
function _py_storage_set(store, key, value) {
    var s = store === 'local' ? _sessionState.localStorage : _sessionState.sessionStorage;
    s[key] = value;
}
function _py_storage_remove(store, key) {
    var s = store === 'local' ? _sessionState.localStorage : _sessionState.sessionStorage;
    delete s[key];
}
function _py_storage_clear(store) {
    if (store === 'local') _sessionState.localStorage = {};
    else _sessionState.sessionStorage = {};
}
function _py_storage_key(store, index) {
    var s = store === 'local' ? _sessionState.localStorage : _sessionState.sessionStorage;
    var keys = Object.keys(s);
    return index < keys.length ? JSON.stringify(keys[index]) : 'null';
}
function _py_storage_length(store) {
    var s = store === 'local' ? _sessionState.localStorage : _sessionState.sessionStorage;
    return Object.keys(s).length;
}

// ── Cookies ──────────────────────────────────────────────────────────────

function _py_get_cookies() { return _sessionState.cookies || ''; }
function _py_set_cookie(cookieStr) {
    _mutationLog.push({type:'setCookie', cookie:cookieStr});
    var pair = cookieStr.split(';')[0];
    if (_sessionState.cookies) _sessionState.cookies += '; ' + pair;
    else _sessionState.cookies = pair;
}

// ── Timers ───────────────────────────────────────────────────────────────

var _timerRegistry = {};
var _timerCounter  = 0;

function _py_set_timeout_ms(delayMs, callbackKey) {
    _timerCounter++;
    var tid = _timerCounter;
    _timerRegistry[tid] = {fireAt: Date.now() + delayMs, key: callbackKey};
    return tid;
}
function _py_clear_timeout(timerId) { delete _timerRegistry[timerId]; }
function _py_get_fired_timers() {
    var now = Date.now(), fired = [];
    for (var tid in _timerRegistry) {
        if (_timerRegistry[tid].fireAt <= now) {
            fired.push(parseInt(tid));
            delete _timerRegistry[tid];
        }
    }
    return JSON.stringify(fired);
}

// ── Fetch ────────────────────────────────────────────────────────────────
// Synchronous fetch is not possible in V8.  The JS fetch() function
// returns a Promise; the bridge queues requests for Python to resolve.

function _py_fetch_sync(url, method, bodyJson, headersJson) {
    // For synchronous callers (XHR), queue and return empty response
    _bridgeCommands.push({type:'fetch', url:url, method:method, bodyJson:bodyJson, headersJson:headersJson});
    return JSON.stringify({ok:false, status:0, text:'', error:'v8_async_only'});
}

var _resolvedFetches = {};

function _py_fetch_async(requestId, url, method, bodyJson, headersJson) {
    _bridgeCommands.push({type:'fetch_async', id:requestId, url:url, method:method, bodyJson:bodyJson, headersJson:headersJson});
}
function _py_fetch_poll(requestId) {
    var r = _resolvedFetches[requestId];
    if (r) return JSON.stringify({resolved:true, result:r});
    return JSON.stringify({resolved:false});
}

// ── Performance ──────────────────────────────────────────────────────────

var _perfStart = Date.now();
function _py_perf_now() { return Date.now() - _perfStart; }

// ── Bridge drains (called by Python after eval) ─────────────────────────

function _drainBridgeCommands() {
    var cmds = JSON.stringify(_bridgeCommands);
    _bridgeCommands = [];
    return cmds;
}

function _getMutationLog() {
    var log = JSON.stringify(_mutationLog);
    _mutationLog = [];
    return log;
}
"""


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
        get: function() {
            if (_domTree.rootId) {
                var raw = _nodeToJson(_domTree.rootId);
                return raw ? _makeElement(JSON.parse(raw)) : null;
            }
            return null;
        },
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

// URL / URLSearchParams polyfill (V8 via PyMiniRacer has no browser globals)
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

// Map, Set, WeakMap, WeakSet - already in V8 but check
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



# ─────────────────────────────────────────────────────────────────────────────
# DOM mutation sync (V8 → Python)
# ─────────────────────────────────────────────────────────────────────────────


def sync_dom_mutations(ctx: Any, session: Any) -> None:
    """
    Read the mutation log from V8 and apply changes to the Python DOM.

    Call this after every script evaluation to keep the Python DOM in
    sync with the JS-side DOM tree.
    """
    try:
        raw = ctx.eval(
            "typeof _getMutationLog === 'function' ? _getMutationLog() : '[]'"
        )
        if not raw or raw == "[]":
            return
        mutations = json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        return

    from an_web.dom.nodes import Element, TextNode

    for mut in mutations:
        mt = mut.get("type", "")
        try:
            if mt == "createElement":
                nid = mut["nodeId"]
                tag = mut["tag"]
                el = Element(node_id=nid, tag=tag, attributes={})
                if not hasattr(session, "_js_created_nodes"):
                    session._js_created_nodes = {}
                session._js_created_nodes[nid] = el

            elif mt == "createTextNode":
                nid = mut["nodeId"]
                text = mut.get("text", "")
                tn = TextNode(node_id=nid, data=text)
                if not hasattr(session, "_js_created_nodes"):
                    session._js_created_nodes = {}
                session._js_created_nodes[nid] = tn

            elif mt == "appendChild":
                parent = _find_node(session, mut["parentId"])
                child = _find_node(session, mut["childId"])
                if parent and child:
                    if child.parent is not None:
                        try:
                            child.parent.children.remove(child)
                        except ValueError:
                            pass
                    parent.append_child(child)
                    doc = getattr(session, "_current_document", None)
                    if doc and isinstance(child, Element):
                        doc.register_element(child)
                    _maybe_queue_dynamic_script(session, child)


            elif mt == "removeChild":
                parent = _find_node(session, mut["parentId"])
                child = _find_node(session, mut["childId"])
                if parent and child:
                    try:
                        parent.remove_child(child)
                    except Exception:
                        pass

            elif mt == "insertBefore":
                parent = _find_node(session, mut["parentId"])
                new_node = _find_node(session, mut["newId"])
                ref_id = mut.get("refId")
                if parent and new_node:
                    if new_node.parent is not None:
                        try:
                            new_node.parent.children.remove(new_node)
                        except ValueError:
                            pass
                    if ref_id and ref_id != "null":
                        ref_node = _find_node(session, ref_id)
                        if ref_node:
                            try:
                                idx = parent.children.index(ref_node)
                                new_node.parent = parent
                                parent.children.insert(idx, new_node)
                            except ValueError:
                                parent.append_child(new_node)
                        else:
                            parent.append_child(new_node)
                    else:
                        parent.append_child(new_node)
                    _maybe_queue_dynamic_script(session, new_node)

            elif mt == "setAttribute":
                el = _find_element_by_id(session, mut["nodeId"])
                if el:
                    el.attributes[mut["name"]] = mut["value"]

            elif mt == "removeAttribute":
                el = _find_element_by_id(session, mut["nodeId"])
                if el:
                    el.attributes.pop(mut["name"], None)

            elif mt == "setInnerHTML":
                el = _find_node(session, mut["nodeId"])
                if el:
                    el.children.clear()
                    html_str = mut.get("html", "")
                    if html_str.strip():
                        try:
                            from an_web.browser.parser import parse_html
                            doc = getattr(session, "_current_document", None)
                            base_url = getattr(
                                session, "_current_url", "about:blank"
                            )
                            frag_doc = parse_html(
                                f"<div>{html_str}</div>", base_url=base_url
                            )
                            for node in frag_doc.iter_descendants():
                                if isinstance(node, Element) and node.tag == "div":
                                    for child in list(node.children):
                                        child.parent = el
                                        el.children.append(child)
                                        if doc and isinstance(child, Element):
                                            doc.register_element(child)
                                            for desc in child.iter_descendants():
                                                if isinstance(desc, Element):
                                                    doc.register_element(desc)
                                    break
                        except Exception as exc:
                            log.debug("sync innerHTML error: %s", exc)

            elif mt == "setTextContent":
                el = _find_node(session, mut["nodeId"])
                if el:
                    el.children.clear()
                    text = mut.get("text", "")
                    if text:
                        tn = TextNode(
                            node_id=f"py_sync_{id(el)}", data=text
                        )
                        el.append_child(tn)

            elif mt == "removeNode":
                target = _find_node(session, mut["nodeId"])
                if target and target.parent:
                    try:
                        target.parent.children.remove(target)
                    except ValueError:
                        pass
                    target.parent = None

            elif mt == "setCookie":
                cookies = getattr(session, "cookies", None)
                url = getattr(session, "_current_url", "about:blank")
                if cookies:
                    cookie_str = mut.get("cookie", "")
                    _set_cookie_from_str(cookies, url, cookie_str)

            elif mt == "setTitle":
                doc = getattr(session, "_current_document", None)
                if doc is not None:
                    doc.title = mut.get("title", "")

        except Exception as exc:
            log.debug("sync mutation '%s' error: %s", mt, exc)

    # Graft orphan JS subtrees into the document body so they appear
    # in iter_descendants().  React/Next.js often builds subtrees in
    # memory and the final attach may be undone by reconciliation,
    # leaving rich content floating outside the tree.
    _graft_orphan_subtrees(session)


def _graft_orphan_subtrees(session: Any) -> None:
    """Attach floating JS-created subtrees to the document body.

    After mutation replay, some JS-created Element subtrees may be
    disconnected from the document tree (their parent chain does not
    reach the Document node).  This happens when React's reconciliation
    removes freshly-rendered content from a container.

    To keep this content accessible for AI extraction, we graft any
    orphan subtree with children into ``<body>``.
    """
    from an_web.dom.nodes import Element

    doc = getattr(session, "_current_document", None)
    if doc is None:
        return
    js_nodes = getattr(session, "_js_created_nodes", {})
    if not js_nodes:
        return

    # Find the <body> element
    body = None
    for node in doc.iter_descendants():
        if isinstance(node, Element) and node.tag == "body":
            body = node
            break
    if body is None:
        return

    grafted = 0
    for nid, node in list(js_nodes.items()):
        if not isinstance(node, Element):
            continue
        # Only graft elements that have children (non-trivial subtrees)
        if not node.children:
            continue
        # Skip if already in the document tree
        if node.parent is not None:
            continue
        # Graft under <body>
        body.append_child(node)
        doc.register_element(node)
        for desc in node.iter_descendants():
            if isinstance(desc, Element):
                doc.register_element(desc)
        grafted += 1

    if grafted:
        log.debug("_graft_orphan_subtrees: grafted %d subtrees", grafted)


def _set_cookie_from_str(cookies: Any, url: str, cookie_str: str) -> None:
    """Parse a cookie string and add it to the cookie jar."""
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
