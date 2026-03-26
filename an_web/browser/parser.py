"""
HTML parser bridge — selectolax (Lexbor) → AN-Web DOM tree.

Primary: selectolax (fast, C-backed Lexbor)
Fallback: html5lib (spec-accurate, slower)
"""
from __future__ import annotations

import itertools
from typing import Any

from an_web.dom.nodes import Document, Element, Node, TextNode
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

    # Use mem_id (stable C-level address) as the key — Python's id() is NOT
    # stable for selectolax nodes because each attribute access creates a new
    # Python wrapper around the same C pointer.
    #
    # The selectolax root maps to doc for parent-chain lookups.
    # However, we now create an actual <html> Element so that
    # document.documentElement works in JavaScript (required by jQuery/Sizzle).
    sl_to_dom: dict[int, Node] = {}

    # Create the <html> element as Document's child
    html_el = Element(node_id=_new_id(), tag="html", attributes={})
    html_el.visibility_state = "visible"
    doc.register_element(html_el)
    doc.append_child(html_el)

    if p.root is not None:
        sl_to_dom[p.root.mem_id] = html_el

    # css("*") yields elements in document order (pre-order DFS)
    for sl_node in p.css("*"):
        tag = (sl_node.tag or "").lower()
        if not tag:
            continue

        node_mem_id = sl_node.mem_id

        # html → map to our html_el (already created above)
        if tag == "html":
            sl_to_dom[node_mem_id] = html_el
            continue

        # head → keep as element under html_el
        if tag == "head":
            attrs_h: dict[str, str] = {}
            if sl_node.attributes:
                attrs_h = {k: (v if v is not None else "") for k, v in sl_node.attributes.items()}
            head_el = Element(node_id=_new_id(), tag="head", attributes=attrs_h)
            head_el.visibility_state = "none"
            doc.register_element(head_el)
            html_el.append_child(head_el)
            sl_to_dom[node_mem_id] = head_el
            continue

        if tag in _SKIP_TAGS:
            # Don't add to DOM, but map to parent so descendants fall correctly
            parent_dom = _find_parent_by_mem_id(sl_node, sl_to_dom, doc)
            sl_to_dom[node_mem_id] = parent_dom
            continue

        # Build attributes
        attrs: dict[str, str] = {}
        if sl_node.attributes:
            attrs = {k: (v if v is not None else "") for k, v in sl_node.attributes.items()}

        el = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        el.visibility_state = compute_visibility(el)
        el.is_interactive = tag in _INTERACTIVE_TAGS

        # script/link tags are invisible but kept in DOM for execution
        if tag in _INVISIBLE_TAGS:
            el.visibility_state = "none"

        doc.register_element(el)
        sl_to_dom[node_mem_id] = el

        parent_dom = _find_parent_by_mem_id(sl_node, sl_to_dom, doc)
        parent_dom.append_child(el)

        # Capture direct (non-child) text content
        # For script tags, capture the full text (inline JS code)
        if tag == "script":
            try:
                full_text = sl_node.text(deep=True, strip=False) or ""
                if full_text.strip():
                    el.append_child(TextNode(node_id=_new_id(), data=full_text))
            except Exception:
                pass
        else:
            direct_text = _direct_text(sl_node)
            if direct_text:
                el.append_child(TextNode(node_id=_new_id(), data=direct_text))

    return doc


def _find_parent_by_mem_id(sl_node: Any, sl_to_dom: dict[int, Node], doc: Document) -> Node:
    """Walk up the selectolax parent chain using mem_id for stable identity."""
    sl_parent = getattr(sl_node, "parent", None)
    while sl_parent is not None:
        mapped = sl_to_dom.get(sl_parent.mem_id)
        if mapped is not None:
            return mapped
        sl_parent = getattr(sl_parent, "parent", None)
    return doc


def _direct_text(sl_node: Any) -> str:
    """Return direct (non-descendant) text content of a selectolax node."""
    try:
        return (sl_node.text(deep=False, strip=True) or "").strip()
    except Exception:
        return ""


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

        if child.text and child.text.strip():
            el.append_child(TextNode(node_id=_new_id(), data=child.text.strip()))

        _walk_etree(child, el, doc)

        if child.tail and child.tail.strip():
            parent.append_child(TextNode(node_id=_new_id(), data=child.tail.strip()))
