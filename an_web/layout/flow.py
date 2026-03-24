"""
Block/inline flow inference for AN-Web layout-lite engine.

No pixel rendering. We infer:
  - display type (block / inline / flex / grid / inline-block / none)
  - bbox_hint — abstract bounding box (x, y, w, h) in logical grid units
    where 1 unit ≈ 1px in a 800-wide viewport at 16px base font
  - z_order_hint — stacking context estimate based on CSS z-index + position
  - LayoutInfo — bundle of all of the above

Grid coordinate system:
    x=0, y is cumulative document-order offset (row index × 20 units)
    w is element content-width estimate
    h is element content-height estimate

These are hints for AI interaction targeting, not for rendering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from an_web.layout.visibility import _parse_inline_style, _parse_px

if TYPE_CHECKING:
    from an_web.dom.nodes import Element, Document

# ── Tag classification ────────────────────────────────────────────────────────

BLOCK_TAGS = frozenset({
    "div", "p", "section", "article", "main", "header", "footer",
    "nav", "aside", "form", "ul", "ol", "li", "table", "tr", "td",
    "th", "thead", "tbody", "tfoot", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "figure", "figcaption", "details", "summary",
    "dialog", "address", "dd", "dl", "dt", "fieldset", "hr",
})

INLINE_TAGS = frozenset({
    "span", "a", "strong", "em", "b", "i", "u", "small", "label",
    "abbr", "code", "kbd", "mark", "q", "s", "sub", "sup", "time",
    "cite", "dfn", "samp", "var", "bdo", "bdi", "wbr",
})

REPLACED_INLINE_TAGS = frozenset({
    "input", "button", "select", "textarea", "img", "video", "audio",
    "canvas", "embed", "object", "progress", "meter",
})

# ── Typical sizes in logical units (px-equivalent at 800w viewport) ───────────
#
# These are median/modal sizes observed on real websites, used as bbox hints.
# Format: (width, height)
_TYPICAL_SIZES: dict[str, tuple[int, int]] = {
    # Input types
    "input:text":     (220, 32),
    "input:email":    (220, 32),
    "input:password": (220, 32),
    "input:search":   (200, 32),
    "input:tel":      (160, 32),
    "input:url":      (240, 32),
    "input:number":   (100, 32),
    "input:date":     (140, 32),
    "input:time":     (100, 32),
    "input:checkbox": (16, 16),
    "input:radio":    (16, 16),
    "input:range":    (160, 24),
    "input:color":    (40, 32),
    "input:file":     (200, 32),
    "input:button":   (100, 32),
    "input:submit":   (100, 32),
    "input:reset":    (80, 32),
    "input:image":    (80, 32),
    "input:hidden":   (0, 0),
    # Other form elements
    "button":         (100, 36),
    "select":         (160, 32),
    "textarea":       (320, 80),
    # Headings (full-width, varying height)
    "h1":             (800, 48),
    "h2":             (800, 36),
    "h3":             (800, 28),
    "h4":             (800, 24),
    "h5":             (800, 20),
    "h6":             (800, 18),
    # Media
    "img":            (300, 200),
    "video":          (640, 360),
    "canvas":         (300, 150),
    # Misc
    "a":              (80, 16),
    "span":           (60, 16),
    "p":              (800, 40),
    "li":             (800, 20),
    "label":          (120, 20),
}

_DEFAULT_BLOCK_SIZE = (800, 20)
_DEFAULT_INLINE_SIZE = (80, 16)
_DEFAULT_SIZE = (100, 20)

# ── Z-order sentinels ─────────────────────────────────────────────────────────

# Role-based z-order hints
_ROLE_Z_ORDER: dict[str, int] = {
    "dialog":      100,
    "alertdialog": 200,
    "tooltip":     80,
    "menu":        90,
    "listbox":     70,
    "alert":       150,
}

_POSITION_Z_ORDER: dict[str, int] = {
    "fixed":    50,
    "sticky":   40,
    "absolute": 10,
    "relative": 1,
}


# ── LayoutInfo result ─────────────────────────────────────────────────────────

@dataclass(slots=True)
class LayoutInfo:
    """
    Layout inferences for a single element.

    All values are estimates — no GPU/paint involved.

    Attributes:
        display_type:   'block' | 'inline' | 'inline-block' | 'flex' | 'grid' |
                        'table' | 'none' | 'unknown'
        bbox_hint:      (x, y, w, h) in logical units (px-equivalent at 800w)
                        x=0, y=document-order offset.  None = unable to estimate.
        z_order_hint:   Stacking context estimate. Higher = painted on top.
                        0 = normal flow, negative = behind normal flow.
        creates_stacking_context: True if this element starts a new stacking context.
    """
    display_type: str
    bbox_hint: tuple[int, int, int, int] | None
    z_order_hint: int
    creates_stacking_context: bool = False


# ── FlowContext ───────────────────────────────────────────────────────────────

class FlowContext:
    """
    Tracks document-order Y position as we walk the DOM.

    Used to populate the `y` component of bbox_hint.
    Reset between calls to compute_document_layout().
    """
    __slots__ = ("_y", "_depth")

    def __init__(self) -> None:
        self._y: int = 0
        self._depth: int = 0

    @property
    def y(self) -> int:
        return self._y

    def advance(self, height: int) -> int:
        """Advance y by height, return y before advancing."""
        current = self._y
        self._y += height
        return current

    def enter(self) -> None:
        self._depth += 1

    def leave(self) -> None:
        self._depth = max(0, self._depth - 1)

    @property
    def depth(self) -> int:
        return self._depth


# ── Public API ────────────────────────────────────────────────────────────────

def get_display_type(element: "Element") -> str:
    """
    Return display type: 'block' | 'inline' | 'inline-block' | 'flex' | 'grid' |
                         'table' | 'none' | 'unknown'

    Priority: inline style > tag default > 'unknown'
    """
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    display = props.get("display", "").lower()
    if display:
        # Normalize compound values
        if display.startswith("inline-"):
            return display  # inline-block, inline-flex, inline-grid
        if display in ("block", "inline", "flex", "grid", "table", "none",
                       "contents", "flow-root", "run-in"):
            return display
        if display.startswith("table-"):
            return "table"
        if display:
            return display  # Pass through unknown values

    tag = element.tag.lower() if hasattr(element, "tag") else ""

    if tag in BLOCK_TAGS:
        return "block"
    if tag in INLINE_TAGS:
        return "inline"
    if tag in REPLACED_INLINE_TAGS:
        if tag == "input":
            inp_type = (element.get_attribute("type") or "text").lower()
            if inp_type == "hidden":
                return "none"
        return "inline-block"

    return "unknown"


def compute_z_order(element: "Element") -> int:
    """
    Estimate z-index / stacking order of an element.

    Algorithm:
    1. Parse explicit z-index from inline style.
    2. If position creates stacking context (absolute/fixed/sticky/relative),
       add a positional bonus.
    3. ARIA role gives additional hint (dialog > tooltip > menu, etc.)

    Returns integer estimate. Higher = painted on top.
    """
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    # Explicit z-index
    z_str = props.get("z-index", "")
    z_explicit: int | None = None
    if z_str and z_str.lower() not in ("auto", "inherit", "initial"):
        try:
            z_explicit = int(z_str)
        except ValueError:
            pass

    if z_explicit is not None:
        return z_explicit

    # Position-based bonus
    position = props.get("position", "").lower()
    pos_bonus = _POSITION_Z_ORDER.get(position, 0)

    # Role-based bonus
    role = element.get_attribute("role") or ""
    role_bonus = _ROLE_Z_ORDER.get(role.lower(), 0)

    # Special tag bonuses
    tag = getattr(element, "tag", "").lower()
    if tag == "dialog":
        role_bonus = max(role_bonus, _ROLE_Z_ORDER["dialog"])

    return pos_bonus + role_bonus


def creates_stacking_context(element: "Element") -> bool:
    """
    Detect if an element creates a CSS stacking context.

    Stacking context triggers (relevant subset):
    - position: absolute/fixed/relative/sticky + non-auto z-index
    - opacity < 1
    - transform, filter, perspective, clip-path (non-none)
    - isolation: isolate
    - will-change (non-auto)
    """
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    position = props.get("position", "").lower()
    z_index = props.get("z-index", "auto").lower()

    if position in ("absolute", "fixed", "relative", "sticky") and z_index != "auto":
        return True

    opacity = props.get("opacity", "1")
    try:
        if float(opacity) < 1.0:
            return True
    except ValueError:
        pass

    for sc_prop in ("transform", "filter", "perspective"):
        val = props.get(sc_prop, "").lower()
        if val and val not in ("none", ""):
            return True

    if props.get("isolation", "").lower() == "isolate":
        return True

    if position == "fixed":
        return True

    tag = getattr(element, "tag", "").lower()
    if tag in ("dialog",):
        return True

    return False


def infer_bbox_hint(
    element: "Element",
    flow_ctx: FlowContext | None = None,
) -> tuple[int, int, int, int]:
    """
    Estimate bounding box of an element in logical units.

    Returns (x, y, w, h):
      x: always 0 (left edge of viewport — we don't track horizontal offsets)
      y: current FlowContext y position (document order)
      w: estimated content width
      h: estimated content height

    Width/height priority:
      1. Inline style width/height (px only)
      2. Tag + type defaults from _TYPICAL_SIZES
      3. Display-type defaults
    """
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    # Start y
    y = flow_ctx.y if flow_ctx is not None else 0

    # Try inline style dimensions first
    style_w = _parse_px(props.get("width", ""))
    style_h = _parse_px(props.get("height", ""))

    if style_w is not None and style_h is not None:
        w, h = int(style_w), int(style_h)
        if flow_ctx is not None:
            flow_ctx.advance(h)
        return (0, y, w, h)

    # Tag-based lookup
    tag = getattr(element, "tag", "").lower()
    lookup_key = tag

    # Refine for <input> by type
    if tag == "input":
        inp_type = (element.get_attribute("type") or "text").lower()
        lookup_key = f"input:{inp_type}"

    if lookup_key in _TYPICAL_SIZES:
        w, h = _TYPICAL_SIZES[lookup_key]
        # Apply inline overrides
        if style_w is not None:
            w = int(style_w)
        if style_h is not None:
            h = int(style_h)
        if flow_ctx is not None:
            flow_ctx.advance(h)
        return (0, y, w, h)

    # Display-type fallback
    display = get_display_type(element)
    if display == "block":
        w, h = _DEFAULT_BLOCK_SIZE
    elif display in ("inline", "inline-block"):
        # Estimate width from text content
        text_len = len(getattr(element, "text_content", "") or "")
        w = min(800, max(20, text_len * 8))
        h = _DEFAULT_INLINE_SIZE[1]
    else:
        w, h = _DEFAULT_SIZE

    if style_w is not None:
        w = int(style_w)
    if style_h is not None:
        h = int(style_h)

    if flow_ctx is not None:
        flow_ctx.advance(h)

    return (0, y, w, h)


def compute_layout_info(
    element: "Element",
    flow_ctx: FlowContext | None = None,
) -> LayoutInfo:
    """
    Compute all layout information for a single element.

    Args:
        element:   DOM Element to analyse.
        flow_ctx:  Optional flow context for Y position tracking.

    Returns LayoutInfo with display_type, bbox_hint, z_order_hint, creates_stacking_context.
    """
    display = get_display_type(element)
    bbox = infer_bbox_hint(element, flow_ctx)
    z = compute_z_order(element)
    sc = creates_stacking_context(element)

    return LayoutInfo(
        display_type=display,
        bbox_hint=bbox,
        z_order_hint=z,
        creates_stacking_context=sc,
    )


def compute_document_layout(doc: "Document") -> dict[str, LayoutInfo]:
    """
    Walk the entire document and compute LayoutInfo for every element.

    Returns a mapping of node_id → LayoutInfo.
    This is a full document pass — call once and cache.
    """
    from an_web.dom.nodes import Element

    ctx = FlowContext()
    result: dict[str, LayoutInfo] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, Element):
            info = compute_layout_info(node, ctx)
            result[node.node_id] = info
            for child in node.children:
                _walk(child)

    for child in doc.children:
        _walk(child)

    return result
