"""
Layout-lite engine for AN-Web.

Not pixel rendering — interactability inference only.
Inspired by Lightpanda's approach: headless means no GPU paint,
but we still need visibility/hit-testing for AI action targeting.

Modules:
    visibility - display:none / visibility:hidden / hidden attribute processing
    hit_test   - Click target disambiguation, overlay/modal priority
    flow       - block/inline flow inference, z-order hints

Primary API: LayoutEngine.assess(element, doc) → ElementAssessment

Quick access:
    from an_web.layout import LayoutEngine, ElementAssessment
    assessment = LayoutEngine().assess(element, doc)
    if assessment.visible and assessment.hit_testable:
        print(f"rank={assessment.interaction_rank:.2f}")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from an_web.layout.flow import (  # noqa: F401
    BLOCK_TAGS,
    INLINE_TAGS,
    FlowContext,
    LayoutInfo,
    compute_document_layout,
    compute_layout_info,
    compute_z_order,
    creates_stacking_context,
    get_display_type,
    infer_bbox_hint,
)
from an_web.layout.hit_test import (  # noqa: F401
    HitTestResult,
    _BlockerInfo,
    _collect_blockers,
    compute_hit_test,
    compute_hit_testable,
    compute_interaction_rank,
    find_click_target,
    rank_elements_for_interaction,
)
from an_web.layout.visibility import (  # noqa: F401
    VisibilityResult,
    _parse_inline_style,
    compute_visibility,
    compute_visibility_cascaded,
    compute_visibility_result,
    get_style_props,
    is_offscreen,
    is_visible,
)

if TYPE_CHECKING:
    from an_web.dom.nodes import Document, Element


# ── ElementAssessment — single unified result ─────────────────────────────────

@dataclass(slots=True)
class ElementAssessment:
    """
    Complete layout assessment for a single DOM element.

    This is what LayoutEngine.assess() returns — the unified view of
    visibility, reachability, size hint, and AI interaction priority.

    Attributes:
        visible:          True if element is visually present.
        hit_testable:     True if pointer events can reach this element.
        visibility_state: 'visible' | 'hidden' | 'none'
        visibility_reason: Human-readable explanation.
        bbox_hint:        (x, y, w, h) in logical units. None if unknown.
        interaction_rank: 0.0–1.0. Higher = better AI target.
        z_order_hint:     Stacking order estimate.
        occluded_by:      node_id of occluding element, if any.
        display_type:     CSS display value inference.
        creates_stacking_context: True if element starts a stacking context.
    """
    visible: bool
    hit_testable: bool
    visibility_state: str
    visibility_reason: str
    bbox_hint: tuple[int, int, int, int] | None
    interaction_rank: float
    z_order_hint: int
    occluded_by: str | None
    display_type: str
    creates_stacking_context: bool

    @property
    def is_actionable(self) -> bool:
        """True when an AI agent can meaningfully interact with this element."""
        return self.visible and self.hit_testable and self.interaction_rank > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "visible": self.visible,
            "hit_testable": self.hit_testable,
            "visibility_state": self.visibility_state,
            "visibility_reason": self.visibility_reason,
            "bbox_hint": list(self.bbox_hint) if self.bbox_hint else None,
            "interaction_rank": round(self.interaction_rank, 4),
            "z_order_hint": self.z_order_hint,
            "occluded_by": self.occluded_by,
            "display_type": self.display_type,
            "creates_stacking_context": self.creates_stacking_context,
        }


# ── LayoutEngine ──────────────────────────────────────────────────────────────

class LayoutEngine:
    """
    Layout-lite engine: combines visibility, flow, and hit-test into one API.

    Usage::

        engine = LayoutEngine()
        assessment = engine.assess(element, doc)

        # Or batch assess all elements in a document
        results = engine.assess_document(doc)

    Batch assessment is significantly more efficient than calling assess()
    repeatedly because blocker collection is done once.
    """

    def assess(
        self,
        element: Element,
        doc: Document,
        flow_ctx: FlowContext | None = None,
        blockers: list[_BlockerInfo] | None = None,
    ) -> ElementAssessment:
        """
        Full layout assessment for a single element.

        Args:
            element:   The DOM element to assess.
            doc:       The document containing the element.
            flow_ctx:  Optional FlowContext for Y position tracking.
            blockers:  Optional pre-collected blocker list (pass for batch mode).

        Returns an ElementAssessment with all layout properties.
        """
        # Visibility (cascaded)
        vis_result: VisibilityResult = compute_visibility_cascaded(element)
        visible = vis_result.is_visible

        # Display type + bbox
        display = get_display_type(element)
        bbox = infer_bbox_hint(element, flow_ctx)

        # Z-order + stacking context
        z = compute_z_order(element)
        sc = creates_stacking_context(element)

        # Hit test (only meaningful for visible elements)
        if not visible:
            return ElementAssessment(
                visible=False,
                hit_testable=False,
                visibility_state=vis_result.state,
                visibility_reason=vis_result.reason,
                bbox_hint=bbox,
                interaction_rank=0.0,
                z_order_hint=z,
                occluded_by=None,
                display_type=display,
                creates_stacking_context=sc,
            )

        hit_ok, occluded_by = compute_hit_testable(element, doc, blockers)
        rank = compute_interaction_rank(element, visible=True, hit_testable=hit_ok, doc=doc)

        return ElementAssessment(
            visible=True,
            hit_testable=hit_ok,
            visibility_state=vis_result.state,
            visibility_reason=vis_result.reason,
            bbox_hint=bbox,
            interaction_rank=rank,
            z_order_hint=z,
            occluded_by=occluded_by,
            display_type=display,
            creates_stacking_context=sc,
        )

    def assess_document(
        self,
        doc: Document,
    ) -> dict[str, ElementAssessment]:
        """
        Assess all elements in the document in a single pass.

        More efficient than repeated assess() calls because:
        - Blocker list is collected once
        - FlowContext is shared across the walk (correct Y positions)

        Returns: node_id → ElementAssessment mapping.
        """
        from an_web.dom.nodes import Element

        blockers = _collect_blockers(doc)
        flow_ctx = FlowContext()
        results: dict[str, ElementAssessment] = {}

        def _walk(node: Any) -> None:
            if isinstance(node, Element):
                assessment = self.assess(node, doc, flow_ctx, blockers)
                results[node.node_id] = assessment
                for child in node.children:
                    _walk(child)

        for child in doc.children:
            _walk(child)

        return results

    def find_interactive_elements(
        self,
        doc: Document,
        min_rank: float = 0.3,
        max_results: int = 20,
    ) -> list[tuple[Element, ElementAssessment]]:
        """
        Find the most actionable elements in the document.

        Returns elements with interaction_rank >= min_rank, sorted descending.
        """
        assessments = self.assess_document(doc)
        scored: list[tuple[Any, ElementAssessment]] = []

        for el in doc.iter_elements():
            a = assessments.get(el.node_id)
            if a and a.interaction_rank >= min_rank:
                scored.append((el, a))

        scored.sort(key=lambda pair: pair[1].interaction_rank, reverse=True)
        return scored[:max_results]
