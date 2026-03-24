"""CSS selector engine for AN-Web DOM."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element


class SelectorEngine:
    """
    CSS selector matching against AN-Web DOM tree.
    Uses cssselect for parsing + custom tree walker for matching.
    """

    def query_selector(self, doc: Document, selector: str) -> Element | None:
        """Return the first matching element."""
        results = self.query_selector_all(doc, selector)
        return results[0] if results else None

    def query_selector_all(self, doc: Document, selector: str) -> list[Element]:
        """Return all matching elements."""
        from an_web.dom.nodes import Element as El
        selector = selector.strip()
        results: list[El] = []
        for element in doc.iter_elements():
            if self._matches(element, selector):
                results.append(element)
        return results

    def _matches(self, element: Element, selector: str) -> bool:
        """Simple selector matching — to be enhanced."""
        from an_web.dom.nodes import Element as El
        selector = selector.strip()

        # ID selector
        if selector.startswith("#"):
            return element.get_id() == selector[1:]

        # Class selector
        if selector.startswith("."):
            return selector[1:] in element.get_class_list()

        # Attribute selector [attr=value]
        if selector.startswith("[") and selector.endswith("]"):
            inner = selector[1:-1]
            if "=" in inner:
                attr, value = inner.split("=", 1)
                value = value.strip("\"'")
                return element.get_attribute(attr.strip()) == value
            return element.has_attribute(inner)

        # Tag selector
        if selector.isidentifier():
            return element.tag == selector.lower()

        return False
