"""
HTML parser bridge — selectolax (Lexbor) → AN-Web DOM tree.

Primary: selectolax (fast, C-backed Lexbor)
Fallback: html5lib (spec-accurate, slower)
"""
from __future__ import annotations

import itertools
from typing import Any

from an_web.dom.nodes import CommentNode, Document, Element, Node, TextNode
from an_web.layout.visibility import compute_visibility

# ─── Unique node ID counter ───────────────────────────────────────────────────

_id_counter = itertools.count(1)


def _new_id() -> str:
    return f"n{next(_id_counter)}"


# ─── Interactive tag set ──────────────────────────────────────────────────────

_INTERACTIVE_TAGS = frozenset({"input", "button", "a", "select", "textarea"})
_SKIP_TAGS = frozenset({"style", "noscript", "meta", "template"})
# script and link tags are preserved in the DOM so that the navigate action
# can discover external <script src="..."> and <link rel="stylesheet"> later.
_INVISIBLE_TAGS = frozenset({"script", "link"})


# ─── Public API ───────────────────────────────────────────────────────────────

def parse_html(html: str, base_url: str = "about:blank") -> Document:
    """
    Parse HTML string into an AN-Web Document tree.

    Tries selectolax/Lexbor first; falls back to html5lib on any error.
    Returns a minimal empty Document on total parse failure.

    After building the tree, propagates inherited visibility (display:none
    from parent → children) so ClickAction can correctly reject hidden targets.
    """
    try:
        doc = _parse_selectolax(html, base_url)
    except Exception:
        try:
            doc = _parse_html5lib(html, base_url)
        except Exception:
            return Document(url=base_url)

    _propagate_visibility(doc)
    return doc


def _propagate_visibility(doc: Document) -> None:
    """
    Inherit visibility_state from parent to children.

    An element is 'none' if any ancestor has visibility_state='none'.
    This matches browser CSS cascade: display:none is inherited.
    """
    _propagate_node(doc, inherited_none=False)


def _propagate_node(node: Any, inherited_none: bool) -> None:
    from an_web.dom.nodes import Element
    for child in node.children:
        if isinstance(child, Element):
            if inherited_none:
                child.visibility_state = "none"
            # Recurse: pass True if this child OR its ancestor is hidden
            _propagate_node(child, inherited_none or child.visibility_state == "none")
        else:
            _propagate_node(child, inherited_none)


# ─── selectolax backend ───────────────────────────────────────────────────────

def _parse_selectolax(html: str, base_url: str) -> Document:
    try:
        from selectolax.lexbor import LexborHTMLParser as Parser  # type: ignore[import]
    except ImportError:
        from selectolax.parser import HTMLParser as Parser  # type: ignore[import]

    p = Parser(html)
    doc = Document(url=base_url)

    title_node = p.css_first("title")
    if title_node:
        doc.title = title_node.text(strip=True)

    # Create the <html> element as Document's child so that
    # document.documentElement works in JavaScript (required by jQuery/Sizzle).
    html_el = Element(node_id=_new_id(), tag="html", attributes={})
    html_el.visibility_state = "visible"
    doc.register_element(html_el)
    doc.append_child(html_el)

    # Walk the raw Lexbor node list (.child/.next) instead of css("*"):
    # css("*") yields only elements, which forced all of an element's direct
    # text to be concatenated into a single TextNode appended BEFORE its
    # element children — destroying the reading order of mixed inline content
    # ("Python is a <a>high-level</a> language" came out reordered).
    # The raw walk preserves text/element interleaving and inter-word
    # whitespace exactly as parsed.
    if p.root is not None:
        _walk_selectolax(p.root, html_el, doc)

    return doc


# Inline-level tags for whitespace-only text-node handling (mirrors
# an_web.dom.nodes._INLINE_TAGS; kept local to avoid a private import cycle).
_PARSER_INLINE_TAGS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em",
    "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small",
    "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
})


def _separates_inline(parent_dom: Node, sl_text_node: Any) -> bool:
    """True if a whitespace-only text node sits between two inline things."""
    prev = parent_dom.children[-1] if parent_dom.children else None
    if prev is None:
        return False
    if not (isinstance(prev, TextNode)
            or (isinstance(prev, Element) and prev.tag in _PARSER_INLINE_TAGS)):
        return False
    nxt = sl_text_node.next
    if nxt is None:
        return False
    nxt_tag = (nxt.tag or "").lower()
    return nxt_tag == "-text" or nxt_tag in _PARSER_INLINE_TAGS


def _walk_selectolax(sl_node: Any, parent_dom: Node, doc: Document) -> None:
    """Convert sl_node's children into DOM nodes under parent_dom, in order."""
    child = sl_node.child
    while child is not None:
        tag = (child.tag or "").lower()

        if tag == "-text":
            raw = child.text_content or ""
            if raw.strip():
                # Keep boundary whitespace of real text intact for correct
                # textContent joins ("foo <b>bar</b>" must not become "foobar").
                parent_dom.append_child(TextNode(node_id=_new_id(), data=raw))
            elif raw and _separates_inline(parent_dom, child):
                # Whitespace-only node BETWEEN inline content is a word break
                # ("</a>\n<b>word</b>" must not merge words). Collapse to a
                # single space; pure inter-block indentation is dropped.
                parent_dom.append_child(TextNode(node_id=_new_id(), data=" "))
            child = child.next
            continue

        if tag == "-comment":
            # Preserve comments: React hydration matches SSR markers like
            # <!--$--> / <!--/$-->; dropping them forces a client re-render
            # that duplicates content. Lexbor exposes no comment-data API,
            # so slice the serialized form.
            h = child.html or ""
            if h.startswith("<!--") and h.endswith("-->"):
                data = h[4:-3]
            else:
                data = ""
            parent_dom.append_child(CommentNode(node_id=_new_id(), data=data))
            child = child.next
            continue

        if not tag or tag.startswith("-"):
            # other non-element nodes (undocumented pseudo-tags)
            child = child.next
            continue

        if tag == "html":
            # Map onto the html_el we already created; descend.
            html_dom = doc.children[0] if doc.children else parent_dom
            _walk_selectolax(child, html_dom, doc)
            child = child.next
            continue

        if tag in _SKIP_TAGS:
            # style/noscript/meta/template: kept as (empty, hidden) elements
            # so the DOM structure matches a real browser — React hydration
            # walks childNodes and bails out on count/tag mismatches (#418).
            # Their content is NOT descended into: style text is CSS that
            # would pollute AI text surfaces, template children live in
            # .content in real DOM, noscript is inert when JS runs.
            attrs_s: dict[str, str] = {}
            if child.attributes:
                attrs_s = {k: (v if v is not None else "")
                           for k, v in child.attributes.items()}
            skip_el = Element(node_id=_new_id(), tag=tag, attributes=attrs_s)
            skip_el.visibility_state = "none"
            doc.register_element(skip_el)
            parent_dom.append_child(skip_el)
            child = child.next
            continue

        attrs: dict[str, str] = {}
        if child.attributes:
            attrs = {k: (v if v is not None else "") for k, v in child.attributes.items()}

        el = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        el.visibility_state = compute_visibility(el)
        el.is_interactive = tag in _INTERACTIVE_TAGS

        if tag == "head":
            el.visibility_state = "none"
        # script/link tags are invisible but kept in DOM for execution
        if tag in _INVISIBLE_TAGS:
            el.visibility_state = "none"

        doc.register_element(el)
        parent_dom.append_child(el)

        if tag == "script":
            # Capture full inline JS source as one raw text node.
            try:
                full_text = child.text(deep=True, strip=False) or ""
                if full_text.strip():
                    el.append_child(TextNode(node_id=_new_id(), data=full_text))
            except Exception:
                pass
        else:
            _walk_selectolax(child, el, doc)

        child = child.next


# ─── html5lib fallback ────────────────────────────────────────────────────────

def _parse_html5lib(html: str, base_url: str) -> Document:
    import html5lib  # type: ignore[import]

    et_root = html5lib.parse(html, treebuilder="etree", namespaceHTMLElements=False)
    doc = Document(url=base_url)

    # Extract title
    for el in et_root.iter():
        tag = _strip_ns(el.tag)
        if tag == "title" and el.text:
            doc.title = el.text.strip()
            break

    _walk_etree(et_root, doc, doc)
    return doc


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _walk_etree(et_node: Any, parent: Node, doc: Document) -> None:
    """Recursively convert an ElementTree subtree into AN-Web DOM nodes."""
    _ETREE_SKIP = frozenset({"style", "noscript", "meta",
                              "head", "template"})

    for child in et_node:
        # Comments/PIs have a callable .tag in ElementTree, not a string.
        if not isinstance(child.tag, str):
            parent.append_child(
                CommentNode(node_id=_new_id(), data=child.text or "")
            )
            if child.tail and child.tail.strip():
                parent.append_child(TextNode(node_id=_new_id(), data=child.tail))
            continue

        tag = _strip_ns(child.tag)

        if not tag or tag in _ETREE_SKIP:
            _walk_etree(child, parent, doc)  # still recurse for body under html
            continue

        if tag == "html":
            _walk_etree(child, doc, doc)
            continue

        attrs: dict[str, str] = {
            (_strip_ns(k) if "}" in k else k): (v or "")
            for k, v in (child.attrib or {}).items()
        }

        el = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        el.visibility_state = compute_visibility(el)
        el.is_interactive = tag in _INTERACTIVE_TAGS

        doc.register_element(el)
        parent.append_child(el)

        # Keep raw text (boundary whitespace matters for textContent joins);
        # only skip whitespace-only nodes.
        if child.text and child.text.strip():
            el.append_child(TextNode(node_id=_new_id(), data=child.text))

        _walk_etree(child, el, doc)

        if child.tail and child.tail.strip():
            parent.append_child(TextNode(node_id=_new_id(), data=child.tail))
