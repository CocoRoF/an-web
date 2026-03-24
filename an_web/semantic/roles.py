"""
ARIA role inference engine for AN-Web semantic layer.

Implements the WAI-ARIA 1.2 implicit role mapping with AI-native extensions.
Priority order:
  1. Explicit aria-role attribute
  2. Input type specialization
  3. Tag-based implicit role
  4. 'generic' fallback

References:
  https://www.w3.org/TR/html-aria/#docconformance
  https://www.w3.org/TR/wai-aria-1.2/#role_definitions
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Element

# ── Tag → implicit ARIA role ──────────────────────────────────────────────────
# Covers the full HTML5/ARIA 1.2 implicit role table.
TAG_TO_ROLE: dict[str, str] = {
    # Interactive
    "a":            "link",
    "button":       "button",
    "textarea":     "textbox",
    "select":       "combobox",
    "option":       "option",
    "optgroup":     "group",

    # Form structural
    "form":         "form",
    "label":        "none",       # label itself is not interactive
    "fieldset":     "group",
    "legend":       "none",
    "datalist":     "listbox",
    "output":       "status",
    "meter":        "meter",
    "progress":     "progressbar",

    # Media / embeds
    "img":          "img",
    "figure":       "figure",
    "figcaption":   "none",
    "video":        "none",
    "audio":        "none",
    "canvas":       "none",

    # Headings
    "h1":           "heading",
    "h2":           "heading",
    "h3":           "heading",
    "h4":           "heading",
    "h5":           "heading",
    "h6":           "heading",

    # List-like
    "ul":           "list",
    "ol":           "list",
    "li":           "listitem",
    "dl":           "list",
    "dt":           "term",
    "dd":           "definition",
    "menu":         "list",

    # Table
    "table":        "table",
    "thead":        "rowgroup",
    "tbody":        "rowgroup",
    "tfoot":        "rowgroup",
    "tr":           "row",
    "td":           "cell",
    "th":           "columnheader",
    "caption":      "caption",

    # Landmark / sectioning
    "nav":          "navigation",
    "main":         "main",
    "header":       "banner",
    "footer":       "contentinfo",
    "aside":        "complementary",
    "section":      "region",
    "article":      "article",
    "search":       "search",
    "address":      "group",

    # Disclosure
    "details":      "group",
    "summary":      "button",      # summary is the clickable trigger of details

    # Dialog / alerts
    "dialog":       "dialog",

    # Typography / text-level
    "blockquote":   "blockquote",
    "p":            "paragraph",
    "hr":           "separator",
    "pre":          "none",
    "code":         "none",
    "abbr":         "none",
    "cite":         "none",
    "kbd":          "none",
    "mark":         "mark",
    "time":         "none",
    "sub":          "subscript",
    "sup":          "superscript",
    "del":          "deletion",
    "ins":          "insertion",

    # Structural
    "div":          "generic",
    "span":         "generic",
    "body":         "generic",
    "html":         "document",

    # Navigation / hyperlinks
    "area":         "link",
    "map":          "none",

    # Misc
    "iframe":       "none",
    "object":       "none",
    "embed":        "none",
    "noscript":     "none",
    "template":     "none",
    "slot":         "none",
}

# ── input[type] → ARIA role ───────────────────────────────────────────────────
INPUT_TYPE_TO_ROLE: dict[str, str] = {
    # Text inputs
    "text":             "textbox",
    "email":            "textbox",
    "password":         "textbox",
    "tel":              "textbox",
    "url":              "textbox",
    "number":           "spinbutton",
    "search":           "searchbox",

    # Date/time (no native ARIA role — closest is textbox)
    "date":             "textbox",
    "time":             "textbox",
    "datetime-local":   "textbox",
    "month":            "textbox",
    "week":             "textbox",

    # Controls
    "range":            "slider",
    "color":            "none",    # no ARIA role
    "file":             "button",

    # Buttons
    "button":           "button",
    "submit":           "button",
    "reset":            "button",
    "image":            "button",

    # Toggles
    "checkbox":         "checkbox",
    "radio":            "radio",

    # Special
    "hidden":           "none",
}

# ── Role classification sets ──────────────────────────────────────────────────

INTERACTIVE_ROLES: frozenset[str] = frozenset({
    "button", "link", "textbox", "combobox", "checkbox",
    "radio", "slider", "searchbox", "spinbutton", "menuitem",
    "menuitemcheckbox", "menuitemradio", "option", "switch",
    "tab", "treeitem", "gridcell", "columnheader",
    "row",       # table rows can be interactive (sortable)
    "progressbar", "meter",
})

CONTENT_ROLES: frozenset[str] = frozenset({
    "heading", "img", "listitem", "cell", "article",
    "blockquote", "paragraph", "mark", "StaticText",
    "term", "definition", "caption", "figure",
    "subscript", "superscript", "deletion", "insertion",
})

STRUCTURAL_ROLES: frozenset[str] = frozenset({
    "none", "generic", "list", "table", "row", "rowgroup",
    "banner", "navigation", "main", "region", "contentinfo",
    "complementary", "form", "group", "separator", "document",
    "status", "search",
})

LANDMARK_ROLES: frozenset[str] = frozenset({
    "banner", "complementary", "contentinfo", "form",
    "main", "navigation", "region", "search",
})

WIDGET_ROLES: frozenset[str] = frozenset({
    "button", "checkbox", "combobox", "gridcell",
    "link", "listbox", "menuitem", "menuitemcheckbox",
    "menuitemradio", "option", "progressbar", "radio",
    "scrollbar", "searchbox", "slider", "spinbutton",
    "switch", "tab", "tabpanel", "textbox", "treeitem",
})


# ── Public API ────────────────────────────────────────────────────────────────

def infer_role(element: "Element") -> str:
    """
    Infer ARIA role for a DOM element.

    Priority:
    1. Explicit aria-role attribute (verbatim)
    2. <input> type specialization via INPUT_TYPE_TO_ROLE
    3. TAG_TO_ROLE implicit mapping
    4. 'generic' fallback
    """
    # 1. Explicit aria-role
    explicit = element.get_attribute("role")
    if explicit:
        return explicit.strip().lower()

    tag = element.tag.lower()

    # 2. Input type specialization
    if tag == "input":
        input_type = (element.get_attribute("type") or "text").lower()
        return INPUT_TYPE_TO_ROLE.get(input_type, "textbox")

    # 3. Tag implicit role
    return TAG_TO_ROLE.get(tag, "generic")


def is_interactive_role(role: str) -> bool:
    """True if AI agents can meaningfully interact with this role."""
    return role in INTERACTIVE_ROLES


def is_structural_role(role: str) -> bool:
    """True if role is structural (landmark, grouping, list)."""
    return role in STRUCTURAL_ROLES


def is_content_role(role: str) -> bool:
    """True if role represents readable content (heading, paragraph, text)."""
    return role in CONTENT_ROLES


def is_landmark_role(role: str) -> bool:
    """True if role is a landmark (navigation, main, banner, etc.)."""
    return role in LANDMARK_ROLES


def get_heading_level(element: "Element") -> int | None:
    """
    Return heading level 1-6 for heading elements.

    Returns None if element is not a heading.
    Checks aria-level attribute first, then tag name.
    """
    tag = element.tag.lower()

    # aria-level overrides tag for explicit heading level
    aria_level = element.get_attribute("aria-level")
    if aria_level:
        try:
            lvl = int(aria_level)
            if 1 <= lvl <= 6:
                return lvl
        except ValueError:
            pass

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return int(tag[1])

    return None


def get_affordances(role: str, element: "Element") -> list[str]:
    """
    Return list of AI-executable actions for a role+element combination.

    Affordance vocabulary:
    - click     — pointer activation (button, link, checkbox, radio, summary)
    - type      — keyboard text entry (textbox, searchbox, spinbutton)
    - clear     — clear current value (textbox, searchbox, spinbutton)
    - select    — choose from options (combobox, listbox)
    - check     — check a checkbox or radio
    - uncheck   — uncheck a checkbox
    - scroll    — scroll a scrollable container
    - focus     — explicitly focus for keyboard nav
    - submit    — trigger form submission (submit button or form)
    - hover     — mouse hover (tooltips, dropdowns)
    """
    affordances: list[str] = []

    # ── Disabled elements have no affordances ─────────────────────────────
    if element.is_disabled():
        return []

    # ── Read-only inputs can be clicked/focused but not typed ─────────────
    read_only = element.get_attribute("readonly") is not None

    # ── Role-based base affordances ────────────────────────────────────────
    if role in ("button", "link"):
        affordances.append("click")

    if role == "summary":
        affordances.append("click")  # summary is the button inside <details>

    if role in ("textbox", "searchbox", "spinbutton"):
        if not read_only:
            affordances.extend(["type", "clear"])
        affordances.append("focus")

    if role == "combobox":
        affordances.extend(["select", "click"])

    if role == "listbox":
        affordances.append("select")

    if role == "checkbox":
        is_checked = element.get_attribute("checked") is not None
        affordances.append("uncheck" if is_checked else "check")
        affordances.append("click")

    if role == "radio":
        affordances.extend(["check", "click"])

    if role == "slider":
        affordances.extend(["click", "type"])

    if role == "switch":
        affordances.append("click")

    if role == "tab":
        affordances.append("click")

    if role == "menuitem":
        affordances.append("click")

    # ── Submit buttons ─────────────────────────────────────────────────────
    tag = element.tag.lower()
    if tag == "button":
        btn_type = (element.get_attribute("type") or "").lower()
        if btn_type == "submit":
            if "submit" not in affordances:
                affordances.append("submit")

    if tag == "input":
        inp_type = (element.get_attribute("type") or "text").lower()
        if inp_type == "submit":
            if "submit" not in affordances:
                affordances.append("submit")

    if tag == "form":
        if "submit" not in affordances:
            affordances.append("submit")

    # ── Scrollable containers ──────────────────────────────────────────────
    # Only mark 'scroll' if element has overflow CSS or is a known scrollable element
    style = element.get_attribute("style") or ""
    if "overflow" in style.lower():
        if "scroll" not in affordances:
            affordances.append("scroll")

    return affordances
