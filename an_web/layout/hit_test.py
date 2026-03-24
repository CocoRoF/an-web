"""
Click target disambiguation and interactability scoring for AN-Web layout-lite.

No pixel rendering — all decisions are based on:
  1. DOM structure (ancestor chain, stacking context)
  2. CSS property inference (z-index, position, visibility)
  3. Semantic signals (role=dialog, [open], aria-modal)
  4. Element type (interactive tags get higher rank)

Key outputs:
  - hit_testable: bool — can a pointer event reach this element?
  - occluded_by:  str | None — node_id of the occluding element (if any)
  - interaction_rank: float 0.0–1.0 — how useful is this element for AI interaction?

Overlay/modal detection algorithm:
  1. Collect all "blocking" elements in the document
     (dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true],
      elements with z-index > 0 AND position:fixed/absolute that cover the viewport)
  2. For each candidate element, check:
     a. Is it a descendant of a blocking element? → NOT occluded
     b. Is there any blocker that is NOT an ancestor? → occluded
  3. The blocker with highest z-order wins (outermost visual layer)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from an_web.layout.visibility import (
    compute_visibility_cascaded,
    VisibilityResult,
    _parse_inline_style,
)
from an_web.layout.flow import compute_z_order, creates_stacking_context

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element

log = logging.getLogger(__name__)

# ── Interactive tag scoring table ─────────────────────────────────────────────
# Higher base score = more likely to be AI target
_INTERACTIVE_TAG_SCORE: dict[str, float] = {
    "button":   0.60,
    "a":        0.55,
    "input":    0.55,
    "select":   0.50,
    "textarea": 0.50,
    "label":    0.25,
    "details":  0.15,
    "summary":  0.20,
}

_INTERACTIVE_INPUT_TYPES: frozenset[str] = frozenset({
    "text", "email", "password", "search", "tel", "url", "number",
    "date", "time", "datetime-local", "month", "week", "range", "color",
    "checkbox", "radio", "file", "submit", "reset", "button",
})

_SUBMIT_BONUS_TAGS: frozenset[str] = frozenset({"button", "input"})

# ARIA roles that are modal/blocking
_BLOCKING_ROLES: frozenset[str] = frozenset({
    "dialog", "alertdialog",
})

# Max depth before we start penalising interaction_rank
_DEPTH_PENALTY_THRESHOLD = 6
_DEPTH_PENALTY_RATE = 0.03

# ── HitTestResult ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class HitTestResult:
    """
    Result of a hit-test for a single element.

    Attributes:
        hit_testable:     True if pointer events can reach this element.
        occluded_by:      node_id of the overlapping element, or None.
        interaction_rank: Float 0.0–1.0. Higher = better AI action target.
        z_order_hint:     Estimated stacking order of this element.
        visibility_state: 'visible' | 'hidden' | 'none'
    """
    hit_testable: bool
    occluded_by: str | None
    interaction_rank: float
    z_order_hint: int
    visibility_state: str

    @property
    def is_actionable(self) -> bool:
        """True if element can be meaningfully interacted with by AI."""
        return self.hit_testable and self.visibility_state == "visible"


# ── Blocker context ───────────────────────────────────────────────────────────

@dataclass
class _BlockerInfo:
    """Internal representation of a modal/overlay blocker."""
    node_id: str
    z_order: int
    kind: str  # 'dialog' | 'modal' | 'overlay' | 'fixed'


def _collect_blockers(doc: "Document") -> list[_BlockerInfo]:
    """
    Collect all elements that could occlude other elements.

    Blocker conditions:
    1. <dialog open> or <dialog> with open attribute
    2. [role=dialog] or [role=alertdialog]
    3. [aria-modal=true]
    4. Elements with position:fixed and z-index > 0 (overlays / popups)
    """
    from an_web.dom.nodes import Element

    blockers: list[_BlockerInfo] = []

    for el in doc.iter_elements():
        kind: str | None = None

        tag = getattr(el, "tag", "")
        attrs = el.attributes if hasattr(el, "attributes") else {}

        # dialog[open]
        if tag == "dialog" and "open" in attrs:
            kind = "dialog"
        # [role=dialog|alertdialog]
        elif attrs.get("role") in _BLOCKING_ROLES:
            kind = "modal"
        # [aria-modal=true]
        elif attrs.get("aria-modal") == "true":
            kind = "modal"
        else:
            # Check for fixed-position high-z overlay
            style = el.get_attribute("style") or ""
            props = _parse_inline_style(style)
            position = props.get("position", "").lower()
            z_str = props.get("z-index", "auto")
            if position == "fixed" and z_str not in ("auto", "0", "", "inherit"):
                try:
                    z_val = int(z_str)
                    if z_val > 0:
                        kind = "overlay"
                except ValueError:
                    pass

        if kind is not None:
            # Visibility check — a hidden dialog doesn't block
            vis = compute_visibility_cascaded(el)
            if vis.state == "none":
                continue
            # Semantic blockers (dialog/modal/aria-modal) always have at least
            # z_order=1 so that normal-flow elements (z_order=0) are occluded.
            css_z = compute_z_order(el)
            effective_z = max(css_z, 1) if kind in ("dialog", "modal") else css_z
            blockers.append(_BlockerInfo(
                node_id=el.node_id,
                z_order=effective_z,
                kind=kind,
            ))

    return blockers


# ── Ancestor utilities ────────────────────────────────────────────────────────

def _get_ancestor_ids(element: "Element") -> frozenset[str]:
    """Return frozenset of node_id for all ancestors (parent chain)."""
    ids: set[str] = set()
    node = getattr(element, "parent", None)
    while node is not None:
        nid = getattr(node, "node_id", None)
        if nid:
            ids.add(nid)
        node = getattr(node, "parent", None)
    return frozenset(ids)


def _get_depth(element: "Element") -> int:
    """Count parent chain depth."""
    depth = 0
    node = getattr(element, "parent", None)
    while node is not None:
        depth += 1
        node = getattr(node, "parent", None)
    return depth


# ── Core hit-test logic ───────────────────────────────────────────────────────

def compute_hit_testable(
    element: "Element",
    doc: "Document",
    blockers: list[_BlockerInfo] | None = None,
) -> tuple[bool, str | None]:
    """
    Determine if pointer events can reach element.

    Returns (hit_testable: bool, occluded_by: str | None).

    Algorithm:
    1. If element has pointer-events:none → not hit-testable.
    2. Collect blockers (lazily if not provided).
    3. An element is NOT occluded if it's a descendant of a blocker (modal).
       An element IS occluded if any blocker exists outside its ancestor chain.
    4. If element's own z-order is >= blocker's z-order, it's not occluded.
    """
    # pointer-events: none
    style = element.get_attribute("style") or ""
    props = _parse_inline_style(style)
    if props.get("pointer-events", "").lower() == "none":
        return False, None

    # Collect blockers
    if blockers is None:
        blockers = _collect_blockers(doc)

    if not blockers:
        return True, None

    # Get own z-order and ancestor chain
    own_z = compute_z_order(element)
    own_node_id = element.node_id
    ancestor_ids = _get_ancestor_ids(element)

    highest_blocker: _BlockerInfo | None = None
    for blocker in blockers:
        # Skip if we ARE the blocker
        if blocker.node_id == own_node_id:
            continue
        # Skip if blocker is one of our ancestors (we're inside the modal)
        if blocker.node_id in ancestor_ids:
            continue
        # Skip if our z-order is >= the blocker (we're painted on top)
        if own_z >= blocker.z_order:
            continue
        # This blocker occludes us
        if highest_blocker is None or blocker.z_order > highest_blocker.z_order:
            highest_blocker = blocker

    if highest_blocker is not None:
        return False, highest_blocker.node_id

    return True, None


# ── Interaction rank ──────────────────────────────────────────────────────────

def compute_interaction_rank(
    element: "Element",
    visible: bool,
    hit_testable: bool,
    doc: "Document | None" = None,
) -> float:
    """
    Compute an interaction rank score in [0.0, 1.0].

    Higher score = more useful for AI interaction targeting.
    Score is 0.0 for invisible, disabled, or fully occluded elements.

    Scoring components:
    - Base:                          0.10
    - Visible + hit_testable:        +0.20
    - Interactive tag:               +0.30..0.50 (table above)
    - Interactive input type:        +0.10
    - Is submit button:              +0.10
    - Has accessible name / label:   +0.05
    - Enabled (not disabled):        required (+0.0 penalty if disabled)
    - Depth penalty:                 -0.03 per level > 6
    - Has id attr (stable selector): +0.02
    """
    # Hard gates
    if not visible:
        return 0.0
    if not hit_testable:
        return 0.0
    if element.is_disabled():
        return 0.0

    tag = getattr(element, "tag", "").lower()

    # Base score
    score = 0.10

    # Visible and reachable bonus
    score += 0.20

    # Interactive tag bonus
    tag_bonus = _INTERACTIVE_TAG_SCORE.get(tag, 0.0)
    score += tag_bonus

    # Input type bonus
    if tag == "input":
        inp_type = (element.get_attribute("type") or "text").lower()
        if inp_type == "hidden":
            return 0.0  # hidden inputs are never actionable by AI
        if inp_type in _INTERACTIVE_INPUT_TYPES:
            score += 0.10
        # Submit button extra bonus
        if inp_type in ("submit", "button"):
            score += 0.10

    # Button with explicit type=submit bonus
    if tag == "button":
        btn_type = (element.get_attribute("type") or "").lower()
        if btn_type == "submit":
            score += 0.10

    # Accessible name helps AI identify targets
    has_name = bool(
        element.get_attribute("aria-label")
        or element.get_attribute("title")
        or element.get_attribute("placeholder")
        or element.text_content.strip()
    )
    if has_name:
        score += 0.05

    # Stable ID
    if element.get_attribute("id"):
        score += 0.02

    # Depth penalty — deeply nested elements are harder to target
    depth = _get_depth(element)
    if depth > _DEPTH_PENALTY_THRESHOLD:
        penalty = (depth - _DEPTH_PENALTY_THRESHOLD) * _DEPTH_PENALTY_RATE
        score -= penalty

    return max(0.0, min(1.0, score))


# ── Public API ────────────────────────────────────────────────────────────────

def compute_hit_test(
    element: "Element",
    doc: "Document",
    blockers: list[_BlockerInfo] | None = None,
) -> HitTestResult:
    """
    Full hit-test for a single element: visibility + occlusion + interaction rank.

    This is the main entry point for the hit-test module.

    Args:
        element:  The DOM element to assess.
        doc:      The document containing the element.
        blockers: Optional pre-collected blocker list (pass for batch efficiency).

    Returns HitTestResult with all computed values.
    """
    # Cascaded visibility
    vis_result = compute_visibility_cascaded(element)
    visible = vis_result.is_visible

    # Z-order
    z = compute_z_order(element)

    # Occlusion check (only needed for visible elements)
    if not visible:
        return HitTestResult(
            hit_testable=False,
            occluded_by=None,
            interaction_rank=0.0,
            z_order_hint=z,
            visibility_state=vis_result.state,
        )

    hit_ok, occluded_by = compute_hit_testable(element, doc, blockers)

    # Interaction rank
    rank = compute_interaction_rank(element, visible=True, hit_testable=hit_ok, doc=doc)

    return HitTestResult(
        hit_testable=hit_ok,
        occluded_by=occluded_by,
        interaction_rank=rank,
        z_order_hint=z,
        visibility_state=vis_result.state,
    )


def find_click_target(
    doc: "Document",
    node_id: str,
    prefer_interactive: bool = True,
) -> "Element | None":
    """
    Find the actual click target element for node_id.

    If the target is occluded, this function returns:
    - The occluding element, if prefer_interactive=True and the occluder is interactive
    - The original target, if the occluder is not interactive (or prefer_interactive=False)
    - None, if node_id is not found at all

    This mirrors Lightpanda's find_click_target() behavior: if a modal covers the
    viewport, clicking should interact with the modal, not the element behind it.
    """
    from an_web.dom.nodes import Element

    target: Element | None = None
    for el in doc.iter_elements():
        if el.node_id == node_id:
            target = el
            break

    if target is None:
        return None

    # Check occlusion
    blockers = _collect_blockers(doc)
    hit_ok, occluded_by_id = compute_hit_testable(target, doc, blockers)

    if hit_ok or not prefer_interactive:
        return target

    # Occluded — find and return the occluding element
    if occluded_by_id:
        for el in doc.iter_elements():
            if el.node_id == occluded_by_id:
                return el

    return target


def rank_elements_for_interaction(
    doc: "Document",
    max_results: int = 10,
) -> list[tuple["Element", HitTestResult]]:
    """
    Walk the document and return top N elements sorted by interaction_rank DESC.

    Useful for AI to find the best action candidates on the current page.
    """
    from an_web.dom.nodes import Element

    blockers = _collect_blockers(doc)
    scored: list[tuple["Element", HitTestResult]] = []

    for el in doc.iter_elements():
        result = compute_hit_test(el, doc, blockers)
        if result.interaction_rank > 0:
            scored.append((el, result))

    scored.sort(key=lambda pair: pair[1].interaction_rank, reverse=True)
    return scored[:max_results]
