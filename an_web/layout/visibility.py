"""CSS visibility computation for AN-Web layout-lite engine."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Element

# CSS properties that make an element invisible
HIDDEN_DISPLAY_VALUES = {"none"}
HIDDEN_VISIBILITY_VALUES = {"hidden", "collapse"}


def compute_visibility(element: Element) -> str:
    """
    Compute visibility state: 'visible' | 'hidden' | 'none'

    Returns:
        'none'    — element takes no space (display:none equivalent)
        'hidden'  — element takes space but invisible
        'visible' — element is visible
    """
    # 1. hidden attribute
    if element.is_hidden():
        return "none"

    # 2. Parse inline style
    style = element.get_attribute("style") or ""
    style_props = _parse_inline_style(style)

    display = style_props.get("display", "").lower()
    if display in HIDDEN_DISPLAY_VALUES:
        return "none"

    visibility = style_props.get("visibility", "").lower()
    if visibility in HIDDEN_VISIBILITY_VALUES:
        return "hidden"

    opacity = style_props.get("opacity", "1")
    try:
        if float(opacity) == 0.0:
            return "hidden"
    except ValueError:
        pass

    # 3. aria-hidden
    aria_hidden = element.get_attribute("aria-hidden")
    if aria_hidden == "true":
        return "hidden"

    return "visible"


def _parse_inline_style(style: str) -> dict[str, str]:
    """Parse CSS inline style string to property dict."""
    props: dict[str, str] = {}
    for declaration in style.split(";"):
        declaration = declaration.strip()
        if ":" in declaration:
            prop, _, value = declaration.partition(":")
            props[prop.strip().lower()] = value.strip()
    return props


def is_offscreen(element: Element) -> bool:
    """Check if element is positioned off-screen."""
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    # position: absolute/fixed with extreme left/top
    position = props.get("position", "")
    if position in ("absolute", "fixed"):
        left = props.get("left", "0")
        top = props.get("top", "0")
        try:
            if float(left.replace("px", "")) < -9000:
                return True
            if float(top.replace("px", "")) < -9000:
                return True
        except ValueError:
            pass

    return False
