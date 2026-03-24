"""
CSS visibility computation for AN-Web layout-lite engine.

Not pixel-accurate — headless means no GPU paint.
Purpose: determine whether an element should be visible to AI agents
for interaction targeting.

Two levels:
  1. compute_visibility(element)          — single element, no ancestor walk
  2. compute_visibility_cascaded(element) — full ancestor chain inheritance
  3. VisibilityResult                     — structured result with reason field
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.dom.nodes import Element

# ── Sentinel CSS values ───────────────────────────────────────────────────────

HIDDEN_DISPLAY_VALUES = frozenset({"none"})
HIDDEN_VISIBILITY_VALUES = frozenset({"hidden", "collapse"})
OFFSCREEN_THRESHOLD = -9000  # px — elements positioned far left/top are screen-reader tricks


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class VisibilityResult:
    """
    Structured result of visibility computation.

    Attributes:
        state:    'visible' | 'hidden' | 'none'
        reason:   Human-readable cause (useful for AI debugging / tracing).
        cascaded: True if the state was inherited from an ancestor.
    """
    state: str
    reason: str
    cascaded: bool = False

    @property
    def is_visible(self) -> bool:
        return self.state == "visible"

    @property
    def is_none(self) -> bool:
        """Element takes no space (display:none equivalent)."""
        return self.state == "none"

    @property
    def is_hidden(self) -> bool:
        """Element takes space but is invisible (visibility:hidden equivalent)."""
        return self.state == "hidden"

    def __str__(self) -> str:
        cascade_note = " (inherited)" if self.cascaded else ""
        return f"VisibilityResult(state={self.state!r}, reason={self.reason!r}{cascade_note})"


# ── Single-element visibility ─────────────────────────────────────────────────

def compute_visibility(element: Element) -> str:
    """
    Compute single-element visibility: 'visible' | 'hidden' | 'none'.

    Does NOT walk ancestors — for full cascade use compute_visibility_cascaded().

    Returns:
        'none'    — element takes no space (display:none equivalent)
        'hidden'  — element takes space but invisible (visibility:hidden)
        'visible' — element is visible
    """
    result = compute_visibility_result(element)
    return result.state


def compute_visibility_result(element: Element) -> VisibilityResult:
    """
    Like compute_visibility() but returns a VisibilityResult with reason.
    """
    # 1. hidden attribute  or  type="hidden"
    if element.is_hidden():
        reason = (
            "input[type=hidden]"
            if getattr(element, "tag", "") == "input" and element.get_attribute("type") == "hidden"
            else "hidden attribute"
        )
        return VisibilityResult(state="none", reason=reason)

    # 2. Parse inline style
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)

    display = props.get("display", "").lower()
    if display in HIDDEN_DISPLAY_VALUES:
        return VisibilityResult(state="none", reason="display:none")

    visibility = props.get("visibility", "").lower()
    if visibility in HIDDEN_VISIBILITY_VALUES:
        return VisibilityResult(state="hidden", reason=f"visibility:{visibility}")

    opacity = props.get("opacity", "1")
    try:
        if float(opacity) == 0.0:
            return VisibilityResult(state="hidden", reason="opacity:0")
    except ValueError:
        pass

    # 3. width:0 AND height:0 → effectively invisible (space-taking but unseen)
    w = _parse_px(props.get("width", ""))
    h = _parse_px(props.get("height", ""))
    if w is not None and h is not None and w == 0 and h == 0:
        return VisibilityResult(state="hidden", reason="width:0;height:0")

    # 4. clip: rect(0,0,0,0) — common "visually hidden" pattern
    clip = props.get("clip", "").lower()
    if "rect(0" in clip or clip == "rect(0,0,0,0)" or clip == "rect(0px,0px,0px,0px)":
        overflow = props.get("overflow", "")
        position = props.get("position", "")
        if position in ("absolute", "fixed") and overflow == "hidden":
            return VisibilityResult(state="hidden", reason="clip:rect(0,0,0,0)")

    # 5. clip-path: none-equivalent patterns
    clip_path = props.get("clip-path", "").lower().strip()
    if clip_path in ("inset(50%)", "inset(100%)", "polygon(0 0,0 0,0 0)"):
        return VisibilityResult(state="hidden", reason=f"clip-path:{clip_path}")

    # 6. aria-hidden
    aria_hidden = element.get_attribute("aria-hidden")
    if aria_hidden == "true":
        return VisibilityResult(state="hidden", reason="aria-hidden:true")

    # 7. Off-screen positioning (SR-only hacks — counts as 'hidden' not 'none')
    if _is_offscreen_style(props):
        return VisibilityResult(state="hidden", reason="off-screen position")

    return VisibilityResult(state="visible", reason="default")


# ── Cascaded visibility ────────────────────────────────────────────────────────

def compute_visibility_cascaded(element: Element) -> VisibilityResult:
    """
    Compute visibility by walking the element's ancestor chain.

    CSS visibility rules implemented:
    - display:none is inherited (children of display:none are also none)
    - visibility:hidden is inherited (children of vis:hidden are also hidden)
      EXCEPT: a child may re-declare visibility:visible to override
    - hidden attribute is inherited in HTML5 semantics

    Returns the most restrictive inherited state.
    """
    # Check self first
    own = compute_visibility_result(element)
    if own.state == "none":
        return own  # display:none — nothing can override

    # Walk ancestors bottom-up
    node = getattr(element, "parent", None)
    while node is not None:
        if not hasattr(node, "tag"):
            # Document or non-Element parent
            break

        ancestor_result = compute_visibility_result(node)  # type: ignore[arg-type]

        if ancestor_result.state == "none":
            return VisibilityResult(
                state="none",
                reason=f"ancestor display:none (ancestor: {getattr(node, 'tag', '?')})",
                cascaded=True,
            )

        if ancestor_result.state == "hidden":
            # visibility:hidden is inherited but can be overridden by child
            # Check if OUR OWN style re-declares visibility:visible
            own_style = element.get_attribute("style") or ""
            own_props = _parse_inline_style(own_style)
            if own_props.get("visibility", "").lower() == "visible":
                pass  # child overrides
            elif own_props.get("visibility", "").lower() in HIDDEN_VISIBILITY_VALUES:
                pass  # child also hidden — already captured in own_result
            else:
                # Inherited visibility:hidden — but only for visibility property
                if "visibility" in ancestor_result.reason.lower() or "hidden" in ancestor_result.reason.lower():
                    return VisibilityResult(
                        state="hidden",
                        reason=f"inherited from ancestor <{getattr(node, 'tag', '?')}>: {ancestor_result.reason}",
                        cascaded=True,
                    )

        node = getattr(node, "parent", None)

    return own


def is_visible(element: Element) -> bool:
    """
    Quick True/False check — uses cascaded visibility.

    Returns True only if element.state == 'visible'.
    """
    return compute_visibility_cascaded(element).is_visible


# ── is_offscreen ──────────────────────────────────────────────────────────────

def is_offscreen(element: Element) -> bool:
    """
    Check if element is positioned far off-screen (SR-only / accessibility hack).

    These elements render as invisible but are meant for screen readers.
    We count them as not visible for AI interaction purposes.
    """
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)
    return _is_offscreen_style(props)


def _is_offscreen_style(props: dict[str, str]) -> bool:
    position = props.get("position", "").lower()
    if position not in ("absolute", "fixed"):
        return False

    left = _parse_px(props.get("left", ""))
    top = _parse_px(props.get("top", ""))

    if left is not None and left < OFFSCREEN_THRESHOLD:
        return True
    if top is not None and top < OFFSCREEN_THRESHOLD:
        return True

    return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_inline_style(style: str) -> dict[str, str]:
    """Parse CSS inline style string to a property → value dict (lowercase keys)."""
    props: dict[str, str] = {}
    for declaration in style.split(";"):
        declaration = declaration.strip()
        if ":" in declaration:
            prop, _, value = declaration.partition(":")
            props[prop.strip().lower()] = value.strip()
    return props


def _parse_px(value: str | None) -> float | None:
    """Parse a CSS pixel value string to float. Returns None if not parseable."""
    if not value:
        return None
    stripped = value.strip().lower().rstrip("px").rstrip()
    try:
        return float(stripped)
    except ValueError:
        return None


def get_style_props(element: Any) -> dict[str, str]:
    """Public helper — parse inline style of any element with get_attribute."""
    style = element.get_attribute("style") or "" if hasattr(element, "get_attribute") else ""
    return _parse_inline_style(style)
