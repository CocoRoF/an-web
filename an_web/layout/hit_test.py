"""Click target disambiguation and overlay detection."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element


def find_click_target(doc: Document, node_id: str) -> Element | None:
    """Find the actual click target, considering overlays."""
    target = None
    for el in doc.iter_elements():
        if el.node_id == node_id:
            target = el
            break

    if target is None:
        return None

    # Check for overlapping elements (modals, overlays)
    # In full implementation: check z-index and bounding boxes
    return target


def is_occluded(element: Element, doc: Document) -> tuple[bool, str | None]:
    """
    Check if element is covered by another element.
    Returns (is_occluded, occluding_node_id).
    """
    # Stub: check for dialog elements that may cover the target
    for el in doc.iter_elements():
        if el.tag == "dialog" and "open" in el.attributes:
            return True, el.node_id
        role = el.get_attribute("role")
        if role in ("dialog", "alertdialog"):
            return True, el.node_id
    return False, None
