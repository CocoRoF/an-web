"""ARIA role inference engine."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Element

# Implicit ARIA roles based on HTML tag
TAG_TO_ROLE: dict[str, str] = {
    "a": "link",
    "button": "button",
    "input": "textbox",       # refined by type below
    "textarea": "textbox",
    "select": "combobox",
    "option": "option",
    "img": "img",
    "h1": "heading", "h2": "heading", "h3": "heading",
    "h4": "heading", "h5": "heading", "h6": "heading",
    "ul": "list", "ol": "list",
    "li": "listitem",
    "table": "table",
    "tr": "row",
    "td": "cell", "th": "columnheader",
    "form": "form",
    "nav": "navigation",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
    "dialog": "dialog",
    "article": "article",
    "section": "region",
    "aside": "complementary",
    "search": "search",
    "checkbox": "checkbox",
    "radio": "radio",
}

INPUT_TYPE_TO_ROLE: dict[str, str] = {
    "button": "button",
    "submit": "button",
    "reset": "button",
    "checkbox": "checkbox",
    "radio": "radio",
    "range": "slider",
    "file": "button",
    "text": "textbox",
    "email": "textbox",
    "password": "textbox",
    "search": "searchbox",
    "tel": "textbox",
    "url": "textbox",
    "number": "spinbutton",
    "date": "textbox",
    "hidden": "none",
}

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox",
    "radio", "slider", "searchbox", "spinbutton", "menuitem",
    "option", "switch", "tab",
}

CONTENT_ROLES = {
    "heading", "img", "listitem", "cell", "columnheader",
    "article", "StaticText",
}

STRUCTURAL_ROLES = {
    "none", "generic", "list", "table", "row", "rowgroup",
    "banner", "navigation", "main", "region", "contentinfo",
    "complementary", "form",
}


def infer_role(element: Element) -> str:
    """Infer ARIA role for an element."""
    # 1. Explicit aria-role attribute
    explicit = element.get_attribute("role")
    if explicit:
        return explicit

    tag = element.tag.lower()

    # 2. Input type specialization
    if tag == "input":
        input_type = element.get_attribute("type") or "text"
        return INPUT_TYPE_TO_ROLE.get(input_type, "textbox")

    # 3. Tag-based implicit role
    return TAG_TO_ROLE.get(tag, "generic")


def is_interactive_role(role: str) -> bool:
    return role in INTERACTIVE_ROLES


def is_structural_role(role: str) -> bool:
    return role in STRUCTURAL_ROLES


def get_affordances(role: str, element: Element) -> list[str]:
    """Get list of possible actions for a role."""
    affordances: list[str] = []

    if role in ("button", "link"):
        affordances.append("click")
    if role in ("textbox", "searchbox", "spinbutton"):
        affordances.extend(["type", "clear"])
    if role == "combobox":
        affordances.extend(["select", "click"])
    if role in ("checkbox", "radio"):
        affordances.append("click")
    if role == "slider":
        affordances.extend(["click", "type"])

    # Scrollable containers
    overflow = element.get_attribute("style") or ""
    if "overflow" in overflow or element.tag in ("div", "section", "main"):
        affordances.append("scroll")

    return affordances
