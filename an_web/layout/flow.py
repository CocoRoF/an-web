"""Block/inline flow inference for layout-lite engine."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Element

BLOCK_TAGS = {
    "div", "p", "section", "article", "main", "header", "footer",
    "nav", "aside", "form", "ul", "ol", "li", "table", "tr", "td",
    "th", "thead", "tbody", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "figure", "figcaption", "details", "summary",
}

INLINE_TAGS = {
    "span", "a", "strong", "em", "b", "i", "u", "small", "label",
    "abbr", "code", "kbd", "mark", "q", "s", "sub", "sup", "time",
}


def get_display_type(element: Element) -> str:
    """Return 'block' | 'inline' | 'flex' | 'grid' | 'none' | 'unknown'."""
    style = element.get_attribute("style") or ""
    for decl in style.split(";"):
        decl = decl.strip()
        if decl.startswith("display:"):
            return decl.split(":", 1)[1].strip().lower()

    tag = element.tag.lower()
    if tag in BLOCK_TAGS:
        return "block"
    if tag in INLINE_TAGS:
        return "inline"
    if tag == "input":
        return "inline"
    if tag in ("button", "select", "textarea"):
        return "inline-block"

    return "unknown"
