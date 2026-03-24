"""
HTML parser bridge — selectolax (Lexbor) → AN-Web DOM tree.

Primary: selectolax (fast, C-backed)
Fallback: html5lib (spec-accurate, slower)

Mirrors Lightpanda's html5ever-based parser/Parser.zig,
but in Python with pluggable backends.
"""
from __future__ import annotations

import uuid
from typing import Any

from an_web.dom.nodes import Document, Element, TextNode
from an_web.layout.visibility import compute_visibility


def parse_html(html: str, base_url: str = "about:blank") -> Document:
    """
    Parse HTML string into AN-Web Document tree.

    Tries selectolax first; falls back to html5lib on parse error.
    """
    try:
        return _parse_selectolax(html, base_url)
    except Exception:
        try:
            return _parse_html5lib(html, base_url)
        except Exception:
            # Last resort: return minimal document
            doc = Document(url=base_url)
            return doc


# ─── Counter for unique node IDs ─────────────────────────────────────────────

_counter = 0


def _new_id() -> str:
    global _counter
    _counter += 1
    return f"n{_counter}"


# ─── selectolax backend ──────────────────────────────────────────────────────

def _parse_selectolax(html: str, base_url: str) -> Document:
    from selectolax.lexbor import LexborHTMLParser  # type: ignore[import]

    parser = LexborHTMLParser(html)
    doc = Document(url=base_url)

    # Extract <title>
    title_node = parser.css_first("title")
    if title_node:
        doc.title = title_node.text(strip=True)

    # Build DOM from selectolax tree
    root = parser.root
    if root:
        _walk_selectolax(root, doc, doc)

    return doc


def _walk_selectolax(
    sl_node: Any,
    parent: Any,
    doc: Document,
) -> None:
    """Recursively walk selectolax nodes into AN-Web DOM."""
    SKIP_TAGS = {"html", "head", "script", "style", "noscript"}

    for child in sl_node.iter():
        if child == sl_node:
            continue

        tag = (child.tag or "").lower()
        if not tag:
            continue

        if tag == "-text":
            text = child.text_content or ""
            if text.strip():
                text_node = TextNode(node_id=_new_id(), data=text)
                parent.append_child(text_node)
            continue

        if tag in SKIP_TAGS:
            continue

        # Build attributes dict
        attrs: dict[str, str] = {}
        try:
            if child.attributes:
                attrs = dict(child.attributes)
        except Exception:
            pass

        element = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        element.visibility_state = compute_visibility(element)

        doc.register_element(element)
        parent.append_child(element)

        # Recurse (direct children only — selectolax iter() is flat, so we stop here)
        # Full recursive walk handled by selectolax's own tree


def _parse_selectolax_recursive(html: str, base_url: str) -> Document:
    """Alternative selectolax parser using css() traversal."""
    try:
        from selectolax.parser import HTMLParser  # type: ignore[import]
    except ImportError:
        from selectolax.lexbor import LexborHTMLParser as HTMLParser  # type: ignore[import]

    parser = HTMLParser(html)
    doc = Document(url=base_url)

    title_node = parser.css_first("title")
    if title_node:
        doc.title = title_node.text(strip=True)

    body = parser.css_first("body")
    if body:
        _convert_selectolax_node(body, doc, doc)

    return doc


def _convert_selectolax_node(sl_node: Any, parent: Any, doc: Document) -> None:
    """Convert a single selectolax node and its subtree."""
    SKIP_TAGS = {"script", "style", "noscript", "meta", "link"}

    for child in sl_node.iter():
        if child == sl_node:
            continue

        tag = (child.tag or "").lower()
        if not tag or tag in SKIP_TAGS:
            continue

        if tag == "-text":
            text = child.text_content or ""
            if text.strip():
                parent.append_child(TextNode(node_id=_new_id(), data=text))
            continue

        attrs: dict[str, str] = {}
        try:
            if child.attributes:
                attrs = {k: v or "" for k, v in child.attributes.items()}
        except Exception:
            pass

        el = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        el.visibility_state = compute_visibility(el)
        doc.register_element(el)
        parent.append_child(el)


# ─── html5lib backend ────────────────────────────────────────────────────────

def _parse_html5lib(html: str, base_url: str) -> Document:
    import html5lib  # type: ignore[import]

    tree_builder = html5lib.parse(html, treebuilder="etree", namespaceHTMLElements=False)
    doc = Document(url=base_url)

    # Walk etree
    _walk_etree(tree_builder, doc, doc)

    return doc


def _walk_etree(etree_node: Any, parent: Any, doc: Document) -> None:
    """Walk an ElementTree node into AN-Web DOM."""
    import xml.etree.ElementTree as ET

    SKIP_TAGS = {"script", "style", "noscript", "meta", "link", "head"}

    for child in etree_node:
        # Strip namespace
        tag = child.tag
        if isinstance(tag, str) and "}" in tag:
            tag = tag.split("}", 1)[1]
        tag = tag.lower()

        if tag in SKIP_TAGS:
            continue

        attrs = {k.split("}", 1)[-1] if "}" in k else k: v
                 for k, v in (child.attrib or {}).items()}

        el = Element(node_id=_new_id(), tag=tag, attributes=attrs)
        el.visibility_state = compute_visibility(el)
        doc.register_element(el)
        parent.append_child(el)

        # Text content
        if child.text and child.text.strip():
            el.append_child(TextNode(node_id=_new_id(), data=child.text))

        _walk_etree(child, el, doc)

        # Tail text (text after closing tag)
        if child.tail and child.tail.strip():
            parent.append_child(TextNode(node_id=_new_id(), data=child.tail))
