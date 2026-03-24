"""
Data extraction action.

Supports three modes:
  1. CSS selector string  →  list of {node_id, tag, text, attributes, html}
  2. Structured dict      →  {"selector": "div.item", "fields": {"title": "h2", "price": ".price"}}
  3. JSON-LD / app JSON   →  {"mode": "json", "selector": "script[type='application/json']"}

All modes return an ActionResult with ``effects["results"]`` and ``effects["count"]``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)


class ExtractAction(Action):
    """
    Extract structured data from the current page.

    Accepted ``query`` formats:

    CSS selector string::

        query = "li.product"

    Structured extraction dict::

        query = {
            "selector": "div.item",
            "fields": {
                "title":  "h2",           # text content of matching child
                "price":  ".price",       # text content
                "href":   {"sel": "a", "attr": "href"},  # attribute
            },
            "mode": "structured",         # optional, inferred if fields present
        }

    JSON extraction::

        query = {"mode": "json", "selector": "script[type='application/json']"}

    HTML extraction::

        query = {"mode": "html", "selector": "main"}

    Effects keys:
    - ``count``:    Number of results.
    - ``results``:  List of extracted items.
    - ``mode``:     Extraction mode used.
    """

    async def execute(
        self,
        session: "Session",
        query: str | dict[str, Any] = "",
        **kwargs: Any,
    ) -> "ActionResult":
        from an_web.dom.semantics import ActionResult

        doc = getattr(session, "_current_document", None)
        if doc is None:
            return self._make_failure(
                "extract",
                "no_document_loaded",
                recommended=[{"tool": "navigate", "note": "load a page first"}],
            )

        # ── Mode dispatch ─────────────────────────────────────────────
        if isinstance(query, dict):
            mode = query.get("mode", "structured" if "fields" in query else "css")
        else:
            mode = "css"

        try:
            if mode == "css":
                results = _extract_css(doc, str(query))
            elif mode == "structured":
                results = _extract_structured(doc, query)  # type: ignore[arg-type]
            elif mode == "json":
                results = _extract_json(doc, query.get("selector", "script[type='application/json']"))  # type: ignore[union-attr]
            elif mode == "html":
                results = _extract_html(doc, query.get("selector", "body"))  # type: ignore[union-attr]
            else:
                return self._make_failure("extract", f"unknown_mode:{mode}")
        except Exception as exc:
            log.debug("ExtractAction error: %s", exc)
            return self._make_failure("extract", f"extraction_error: {exc}")

        return ActionResult(
            status="ok",
            action="extract",
            target=str(query),
            effects={
                "count": len(results),
                "results": results,
                "mode": mode,
            },
        )


# ─── CSS mode ──────────────────────────────────────────────────────────────────


def _extract_css(doc: Any, selector: str) -> list[dict[str, Any]]:
    """Return flat list of matching element summaries."""
    from an_web.dom.document import query_selector_all
    elements = query_selector_all(doc, selector)
    results = []
    for el in elements:
        results.append(_element_summary(el))
    return results


def _element_summary(el: Any) -> dict[str, Any]:
    """Build a summary dict for a single element."""
    text = ""
    if hasattr(el, "inner_text"):
        text = el.inner_text
    elif hasattr(el, "text_content"):
        text = el.text_content or ""

    attrs = {}
    if hasattr(el, "attributes"):
        attrs = dict(el.attributes) if el.attributes else {}

    return {
        "node_id": getattr(el, "node_id", ""),
        "tag": getattr(el, "tag", ""),
        "text": text.strip(),
        "attributes": attrs,
    }


# ─── Structured mode ───────────────────────────────────────────────────────────


def _extract_structured(doc: Any, query: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract a list of items, each with named fields resolved by sub-selectors.

    ``query`` shape::

        {
            "selector": "div.product",
            "fields": {
                "name":  "h2",
                "price": ".price",
                "url":   {"sel": "a", "attr": "href"},
            }
        }
    """
    from an_web.dom.document import query_selector_all, query_selector

    container_sel = query_selector.func if hasattr(query_selector, "func") else query_selector
    root_sel = query.get("selector", "*")
    fields: dict[str, Any] = query.get("fields", {})

    elements = query_selector_all(doc, root_sel)
    results = []

    for el in elements:
        item: dict[str, Any] = {"node_id": getattr(el, "node_id", "")}
        for field_name, field_def in fields.items():
            if isinstance(field_def, str):
                # text content of matching descendant
                child = _query_in(el, field_def)
                if child is not None:
                    item[field_name] = _text(child)
                else:
                    item[field_name] = None
            elif isinstance(field_def, dict):
                sel = field_def.get("sel", "")
                attr = field_def.get("attr", None)
                child = _query_in(el, sel) if sel else el
                if child is not None:
                    if attr:
                        item[field_name] = (
                            child.get_attribute(attr)
                            if hasattr(child, "get_attribute")
                            else None
                        )
                    else:
                        item[field_name] = _text(child)
                else:
                    item[field_name] = None
        results.append(item)

    return results


def _query_in(element: Any, selector: str) -> Any:
    """Query within a specific element's subtree."""
    if not selector:
        return element
    # Use iter_descendants for a simple linear scan (no full CSS engine on subtrees)
    if not hasattr(element, "iter_descendants"):
        return None
    # Try exact tag match first for performance
    for desc in element.iter_descendants():
        tag = getattr(desc, "tag", "")
        # Minimal matching: support simple "tag", ".class", "#id", "tag.class"
        if _matches_simple_selector(desc, selector):
            return desc
    return None


def _matches_simple_selector(el: Any, selector: str) -> bool:
    """
    Minimal CSS selector matching for structured extraction sub-selectors.

    Supports: tag, .class, #id, tag.class, tag#id.
    """
    selector = selector.strip()
    if not selector or selector == "*":
        return True

    tag = getattr(el, "tag", "")
    attrs = el.attributes if hasattr(el, "attributes") and el.attributes else {}

    # Parse #id
    if "#" in selector:
        parts = selector.split("#", 1)
        tag_part = parts[0].lower()
        rest = parts[1]
        # rest may still have .class
        if "." in rest:
            id_part, class_part = rest.split(".", 1)
        else:
            id_part, class_part = rest, None
        if tag_part and tag.lower() != tag_part:
            return False
        if attrs.get("id", "") != id_part:
            return False
        if class_part:
            el_classes = set((attrs.get("class", "") or "").split())
            if class_part not in el_classes:
                return False
        return True

    # Parse .class
    if selector.startswith("."):
        class_name = selector[1:]
        el_classes = set((attrs.get("class", "") or "").split())
        return class_name in el_classes

    # Parse tag.class
    if "." in selector:
        tag_part, class_part = selector.split(".", 1)
        el_classes = set((attrs.get("class", "") or "").split())
        return tag.lower() == tag_part.lower() and class_part in el_classes

    # Plain tag
    return tag.lower() == selector.lower()


def _text(el: Any) -> str:
    if hasattr(el, "inner_text"):
        return (el.inner_text or "").strip()
    return (getattr(el, "text_content", "") or "").strip()


# ─── JSON mode ─────────────────────────────────────────────────────────────────


def _extract_json(doc: Any, selector: str) -> list[dict[str, Any]]:
    """
    Extract parsed JSON from <script type="application/json"> (or similar) tags.

    Falls back to iter_descendants() since some parsers strip <script> from
    the CSS-queryable tree. Returns list of dicts, one per matching element.
    If JSON parse fails, returns the raw text as ``{"raw": "..."}`.
    """
    from an_web.dom.document import query_selector_all
    from an_web.dom.nodes import Element

    results = []

    # First try CSS selector (works if parser preserves script tags)
    elements = query_selector_all(doc, selector)
    if not elements:
        # Fallback: walk all descendants and match manually.
        # Parses the selector to determine tag + type constraint.
        # Common case: "script[type='application/json']" or "script[type='application/ld+json']"
        target_tag = "script"
        target_type: str | None = None
        if "script" in selector and "application/json" in selector:
            target_type = "application/json"
        elif "script" in selector and "application/ld+json" in selector:
            target_type = "application/ld+json"

        for node in doc.iter_descendants():
            if not isinstance(node, Element):
                continue
            if node.tag != target_tag:
                continue
            if target_type is not None:
                node_type = (node.get_attribute("type") or "").lower()
                if node_type != target_type:
                    continue
            elements.append(node)  # type: ignore[attr-defined]

    for el in elements:
        raw = (getattr(el, "text_content", "") or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        results.append({
            "node_id": getattr(el, "node_id", ""),
            "data": parsed,
        })
    return results


# ─── HTML mode ─────────────────────────────────────────────────────────────────


def _extract_html(doc: Any, selector: str) -> list[dict[str, Any]]:
    """
    Extract the outer HTML string of matching elements.

    Falls back to text_content if outer_html is not available.
    """
    from an_web.dom.document import query_selector_all
    elements = query_selector_all(doc, selector)
    results = []
    for el in elements:
        html = ""
        if hasattr(el, "outer_html"):
            html = el.outer_html or ""
        elif hasattr(el, "inner_html"):
            html = el.inner_html or ""
        elif hasattr(el, "text_content"):
            html = el.text_content or ""
        results.append({
            "node_id": getattr(el, "node_id", ""),
            "html": html,
        })
    return results
