"""Document API — querySelector, getElementById, etc."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element


def query_selector(doc: Document, selector: str) -> Element | None:
    from an_web.dom.selectors import SelectorEngine
    return SelectorEngine().query_selector(doc, selector)


def query_selector_all(doc: Document, selector: str) -> list[Element]:
    from an_web.dom.selectors import SelectorEngine
    return SelectorEngine().query_selector_all(doc, selector)


def get_element_by_id(doc: Document, element_id: str) -> Element | None:
    return doc.get_element_by_id(element_id)


def get_elements_by_tag(doc: Document, tag: str) -> list[Element]:
    from an_web.dom.nodes import Element as El
    tag = tag.lower()
    return [e for e in doc.iter_elements() if isinstance(e, El) and e.tag == tag]


def get_forms(doc: Document) -> list[Element]:
    return get_elements_by_tag(doc, "form")


def get_inputs(doc: Document) -> list[Element]:
    from an_web.dom.nodes import Element as El
    INPUT_TAGS = {"input", "textarea", "select"}
    return [e for e in doc.iter_elements() if isinstance(e, El) and e.tag in INPUT_TAGS]


def get_links(doc: Document) -> list[Element]:
    return get_elements_by_tag(doc, "a")


def get_buttons(doc: Document) -> list[Element]:
    from an_web.dom.nodes import Element as El
    results = get_elements_by_tag(doc, "button")
    # Also include input[type=submit] and input[type=button]
    for el in doc.iter_elements():
        if isinstance(el, El) and el.tag == "input":
            if el.get_attribute("type") in ("submit", "button", "reset"):
                results.append(el)
    return results
