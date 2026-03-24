"""Unit tests for layout-lite hit-test and interaction rank engine."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element, Document
from an_web.layout.hit_test import (
    HitTestResult,
    compute_hit_test,
    compute_hit_testable,
    compute_interaction_rank,
    find_click_target,
    rank_elements_for_interaction,
    _collect_blockers,
    _get_ancestor_ids,
    _get_depth,
)
from an_web.layout import LayoutEngine, ElementAssessment


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _el(
    node_id: str = "n1",
    tag: str = "div",
    attrs: dict | None = None,
    parent: Element | None = None,
) -> Element:
    el = Element(node_id=node_id, tag=tag, attributes=attrs or {})
    if parent is not None:
        parent.append_child(el)
    return el


def _doc(*elements: Element) -> Document:
    doc = Document(url="https://example.com")
    for el in elements:
        doc.append_child(el)
    return doc


# ─── HitTestResult ────────────────────────────────────────────────────────────

class TestHitTestResult:
    def test_is_actionable_true(self):
        r = HitTestResult(
            hit_testable=True, occluded_by=None,
            interaction_rank=0.5, z_order_hint=0, visibility_state="visible",
        )
        assert r.is_actionable is True

    def test_is_actionable_false_hidden(self):
        r = HitTestResult(
            hit_testable=True, occluded_by=None,
            interaction_rank=0.5, z_order_hint=0, visibility_state="hidden",
        )
        assert r.is_actionable is False

    def test_is_actionable_false_not_hit_testable(self):
        r = HitTestResult(
            hit_testable=False, occluded_by="modal",
            interaction_rank=0.3, z_order_hint=0, visibility_state="visible",
        )
        assert r.is_actionable is False


# ─── compute_hit_testable ─────────────────────────────────────────────────────

class TestComputeHitTestable:
    def test_no_blockers(self):
        el = _el("n1", "button")
        doc = _doc(el)
        hit_ok, occ = compute_hit_testable(el, doc)
        assert hit_ok is True
        assert occ is None

    def test_pointer_events_none(self):
        el = _el("n1", "button", attrs={"style": "pointer-events: none"})
        doc = _doc(el)
        hit_ok, occ = compute_hit_testable(el, doc)
        assert hit_ok is False
        assert occ is None

    def test_dialog_open_occludes_outside_elements(self):
        modal = _el("m1", "dialog", attrs={"open": ""})
        behind = _el("b1", "button")
        doc = _doc(modal, behind)

        hit_ok, occ = compute_hit_testable(behind, doc)
        assert hit_ok is False
        assert occ == "m1"

    def test_element_inside_dialog_not_occluded(self):
        """Element INSIDE the dialog should not be occluded by the dialog itself."""
        modal = _el("m1", "dialog", attrs={"open": ""})
        inside_btn = _el("ib1", "button")
        modal.append_child(inside_btn)
        doc = _doc(modal)

        hit_ok, occ = compute_hit_testable(inside_btn, doc)
        assert hit_ok is True
        assert occ is None

    def test_role_dialog_occludes(self):
        overlay = _el("o1", "div", attrs={"role": "dialog"})
        behind = _el("b1", "button")
        doc = _doc(overlay, behind)

        hit_ok, occ = compute_hit_testable(behind, doc)
        assert hit_ok is False
        assert occ == "o1"

    def test_aria_modal_occludes(self):
        modal = _el("m1", "div", attrs={"aria-modal": "true"})
        behind = _el("b1", "input", attrs={"type": "text"})
        doc = _doc(modal, behind)

        hit_ok, _ = compute_hit_testable(behind, doc)
        assert hit_ok is False

    def test_high_z_fixed_overlay_occludes(self):
        overlay = _el("o1", "div", attrs={"style": "position: fixed; z-index: 9999"})
        behind = _el("b1", "button")
        doc = _doc(overlay, behind)

        hit_ok, occ = compute_hit_testable(behind, doc)
        assert hit_ok is False
        assert occ == "o1"

    def test_element_with_higher_z_not_occluded(self):
        overlay = _el("o1", "div", attrs={"style": "position: fixed; z-index: 100"})
        front = _el("f1", "button", attrs={"style": "z-index: 200"})
        doc = _doc(overlay, front)

        hit_ok, occ = compute_hit_testable(front, doc)
        assert hit_ok is True

    def test_hidden_dialog_not_a_blocker(self):
        """A dialog that is display:none should NOT occlude elements."""
        hidden_modal = _el("m1", "dialog", attrs={"open": "", "style": "display:none"})
        btn = _el("b1", "button")
        doc = _doc(hidden_modal, btn)

        hit_ok, occ = compute_hit_testable(btn, doc)
        assert hit_ok is True


# ─── compute_interaction_rank ─────────────────────────────────────────────────

class TestComputeInteractionRank:
    def test_invisible_element_zero_rank(self):
        el = _el("n1", "button")
        rank = compute_interaction_rank(el, visible=False, hit_testable=True)
        assert rank == 0.0

    def test_not_hit_testable_zero_rank(self):
        el = _el("n1", "button")
        rank = compute_interaction_rank(el, visible=True, hit_testable=False)
        assert rank == 0.0

    def test_disabled_zero_rank(self):
        el = _el("n1", "button", attrs={"disabled": ""})
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank == 0.0

    def test_button_high_rank(self):
        el = _el("n1", "button")
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank > 0.5

    def test_input_text_high_rank(self):
        el = _el("n1", "input", attrs={"type": "text"})
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank > 0.5

    def test_link_high_rank(self):
        el = _el("n1", "a", attrs={"href": "/page"})
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank > 0.5

    def test_div_lower_rank(self):
        el = _el("n1", "div")
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        # div has no interactive bonus
        assert rank < 0.5

    def test_input_hidden_zero_rank(self):
        el = _el("n1", "input", attrs={"type": "hidden"})
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank == 0.0

    def test_submit_button_highest_rank(self):
        btn = _el("n1", "button", attrs={"type": "submit"})
        generic = _el("n2", "button")
        r_submit = compute_interaction_rank(btn, visible=True, hit_testable=True)
        r_generic = compute_interaction_rank(generic, visible=True, hit_testable=True)
        assert r_submit >= r_generic

    def test_named_element_higher_rank(self):
        named = _el("n1", "button", attrs={"aria-label": "Submit form"})
        unnamed = _el("n2", "button")
        r_named = compute_interaction_rank(named, visible=True, hit_testable=True)
        r_unnamed = compute_interaction_rank(unnamed, visible=True, hit_testable=True)
        assert r_named > r_unnamed

    def test_id_gives_small_bonus(self):
        with_id = _el("n1", "button", attrs={"id": "btn-submit"})
        without = _el("n2", "button")
        assert compute_interaction_rank(with_id, True, True) > compute_interaction_rank(without, True, True)

    def test_deeply_nested_penalized(self):
        """Very deeply nested elements get a small rank penalty."""
        # Build 10-level deep nesting
        root = _el("r", "div")
        current = root
        for i in range(10):
            child = _el(f"c{i}", "div")
            current.append_child(child)
            current = child
        deep_btn = _el("deep_btn", "button")
        current.append_child(deep_btn)

        shallow_btn = _el("shallow_btn", "button")
        root.append_child(shallow_btn)

        r_deep = compute_interaction_rank(deep_btn, True, True)
        r_shallow = compute_interaction_rank(shallow_btn, True, True)
        assert r_shallow > r_deep

    def test_rank_capped_at_one(self):
        el = _el("n1", "button", attrs={
            "type": "submit", "id": "sb", "aria-label": "Submit",
        })
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank <= 1.0

    def test_rank_non_negative(self):
        el = _el("n1", "div")
        rank = compute_interaction_rank(el, visible=True, hit_testable=True)
        assert rank >= 0.0


# ─── compute_hit_test ─────────────────────────────────────────────────────────

class TestComputeHitTest:
    def test_basic_button(self):
        el = _el("n1", "button")
        doc = _doc(el)
        result = compute_hit_test(el, doc)
        assert isinstance(result, HitTestResult)
        assert result.hit_testable is True
        assert result.interaction_rank > 0.5

    def test_hidden_element(self):
        el = _el("n1", "button", attrs={"style": "display:none"})
        doc = _doc(el)
        result = compute_hit_test(el, doc)
        assert result.hit_testable is False
        assert result.interaction_rank == 0.0
        assert result.visibility_state == "none"

    def test_occluded_element(self):
        modal = _el("m1", "dialog", attrs={"open": ""})
        behind = _el("b1", "button")
        doc = _doc(modal, behind)
        result = compute_hit_test(behind, doc)
        assert result.hit_testable is False
        assert result.occluded_by == "m1"

    def test_visible_interactive_has_rank(self):
        el = _el("n1", "input", attrs={"type": "email", "id": "email"})
        doc = _doc(el)
        result = compute_hit_test(el, doc)
        assert result.is_actionable
        assert result.interaction_rank > 0


# ─── find_click_target ────────────────────────────────────────────────────────

class TestFindClickTarget:
    def test_finds_element_by_node_id(self):
        el = _el("n1", "button")
        doc = _doc(el)
        found = find_click_target(doc, "n1")
        assert found is el

    def test_returns_none_for_unknown_id(self):
        el = _el("n1", "button")
        doc = _doc(el)
        found = find_click_target(doc, "nonexistent")
        assert found is None

    def test_returns_blocker_when_occluded_prefer_interactive(self):
        """When target is occluded by modal, return the modal."""
        modal = _el("m1", "dialog", attrs={"open": ""})
        behind = _el("b1", "button")
        doc = _doc(modal, behind)

        found = find_click_target(doc, "b1", prefer_interactive=True)
        assert found is modal

    def test_returns_original_when_prefer_interactive_false(self):
        modal = _el("m1", "dialog", attrs={"open": ""})
        behind = _el("b1", "button")
        doc = _doc(modal, behind)

        found = find_click_target(doc, "b1", prefer_interactive=False)
        assert found is behind

    def test_non_occluded_returns_self(self):
        el = _el("n1", "input", attrs={"type": "text"})
        doc = _doc(el)
        found = find_click_target(doc, "n1")
        assert found is el


# ─── rank_elements_for_interaction ───────────────────────────────────────────

class TestRankElementsForInteraction:
    def test_returns_list_sorted_by_rank(self):
        btn = Element(node_id="btn1", tag="button", attributes={})
        div = Element(node_id="div1", tag="div", attributes={})
        doc = _doc(btn, div)

        ranked = rank_elements_for_interaction(doc)
        assert len(ranked) > 0
        # Verify sorted
        ranks = [r.interaction_rank for _, r in ranked]
        assert ranks == sorted(ranks, reverse=True)

    def test_interactive_elements_at_top(self):
        btn = Element(node_id="btn1", tag="button", attributes={})
        inp = Element(node_id="inp1", tag="input", attributes={"type": "text"})
        div = Element(node_id="div1", tag="div", attributes={})
        doc = _doc(btn, inp, div)

        ranked = rank_elements_for_interaction(doc, max_results=3)
        top_ids = [el.node_id for el, _ in ranked]
        assert "btn1" in top_ids
        assert "inp1" in top_ids

    def test_max_results_respected(self):
        els = [Element(node_id=f"btn{i}", tag="button", attributes={}) for i in range(20)]
        doc = _doc(*els)
        ranked = rank_elements_for_interaction(doc, max_results=5)
        assert len(ranked) <= 5

    def test_hidden_elements_excluded(self):
        hidden = Element(node_id="h1", tag="button", attributes={"style": "display:none"})
        visible = Element(node_id="v1", tag="button", attributes={})
        doc = _doc(hidden, visible)

        ranked = rank_elements_for_interaction(doc)
        ids = [el.node_id for el, _ in ranked]
        assert "h1" not in ids
        assert "v1" in ids


# ─── _collect_blockers ───────────────────────────────────────────────────────

class TestCollectBlockers:
    def test_no_blockers_empty(self):
        btn = _el("n1", "button")
        doc = _doc(btn)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 0

    def test_dialog_open_is_blocker(self):
        modal = _el("m1", "dialog", attrs={"open": ""})
        doc = _doc(modal)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 1
        assert blockers[0].node_id == "m1"
        assert blockers[0].kind == "dialog"

    def test_dialog_without_open_not_blocker(self):
        modal = _el("m1", "dialog")  # no 'open' attribute
        doc = _doc(modal)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 0

    def test_role_dialog_is_blocker(self):
        el = _el("m1", "div", attrs={"role": "dialog"})
        doc = _doc(el)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 1

    def test_aria_modal_is_blocker(self):
        el = _el("m1", "div", attrs={"aria-modal": "true"})
        doc = _doc(el)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 1

    def test_fixed_high_z_is_blocker(self):
        el = _el("o1", "div", attrs={"style": "position: fixed; z-index: 999"})
        doc = _doc(el)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 1
        assert blockers[0].kind == "overlay"

    def test_hidden_dialog_not_a_blocker(self):
        modal = _el("m1", "dialog", attrs={"open": "", "style": "display:none"})
        doc = _doc(modal)
        blockers = _collect_blockers(doc)
        assert len(blockers) == 0


# ─── _get_ancestor_ids / _get_depth ──────────────────────────────────────────

class TestAncestorHelpers:
    def test_get_ancestor_ids_no_parent(self):
        el = _el()
        ids = _get_ancestor_ids(el)
        assert len(ids) == 0

    def test_get_ancestor_ids_with_parent(self):
        parent = _el("p1", "div")
        child = _el("c1", "span")
        parent.append_child(child)
        ids = _get_ancestor_ids(child)
        assert "p1" in ids

    def test_get_ancestor_ids_deep(self):
        gp = _el("gp", "div")
        p = _el("p", "div")
        c = _el("c", "span")
        gp.append_child(p)
        p.append_child(c)
        ids = _get_ancestor_ids(c)
        assert "gp" in ids
        assert "p" in ids

    def test_get_depth_no_parent(self):
        el = _el()
        assert _get_depth(el) == 0

    def test_get_depth_one_level(self):
        parent = _el("p", "div")
        child = _el("c", "span")
        parent.append_child(child)
        assert _get_depth(child) == 1

    def test_get_depth_nested(self):
        root = _el("r", "div")
        l1 = _el("l1", "div")
        l2 = _el("l2", "div")
        root.append_child(l1)
        l1.append_child(l2)
        assert _get_depth(l2) == 2


# ─── LayoutEngine ─────────────────────────────────────────────────────────────

class TestLayoutEngine:
    def test_assess_returns_element_assessment(self):
        el = _el("n1", "button")
        doc = _doc(el)
        engine = LayoutEngine()
        assessment = engine.assess(el, doc)
        assert isinstance(assessment, ElementAssessment)

    def test_assess_visible_button(self):
        el = _el("n1", "button")
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        assert a.visible is True
        assert a.hit_testable is True
        assert a.interaction_rank > 0.5
        assert a.bbox_hint is not None

    def test_assess_hidden_element(self):
        el = _el("n1", "button", attrs={"style": "display:none"})
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        assert a.visible is False
        assert a.hit_testable is False
        assert a.interaction_rank == 0.0

    def test_assess_document_returns_all(self):
        btn = Element(node_id="btn1", tag="button", attributes={})
        inp = Element(node_id="inp1", tag="input", attributes={"type": "text"})
        doc = Document(url="https://example.com")
        doc.append_child(btn)
        doc.append_child(inp)

        engine = LayoutEngine()
        results = engine.assess_document(doc)
        assert "btn1" in results
        assert "inp1" in results

    def test_find_interactive_elements(self):
        btn = Element(node_id="btn1", tag="button", attributes={})
        div = Element(node_id="div1", tag="div", attributes={})
        doc = Document(url="https://example.com")
        doc.append_child(btn)
        doc.append_child(div)

        engine = LayoutEngine()
        interactive = engine.find_interactive_elements(doc, min_rank=0.3)
        ids = [el.node_id for el, _ in interactive]
        assert "btn1" in ids

    def test_assessment_is_actionable(self):
        el = _el("n1", "button", attrs={"id": "btn"})
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        assert a.is_actionable is True

    def test_assessment_to_dict(self):
        el = _el("n1", "button")
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        d = a.to_dict()
        assert "visible" in d
        assert "hit_testable" in d
        assert "interaction_rank" in d
        assert "bbox_hint" in d
        assert "display_type" in d

    def test_disabled_element_zero_rank(self):
        el = _el("n1", "button", attrs={"disabled": ""})
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        assert a.interaction_rank == 0.0

    def test_select_has_good_rank(self):
        el = _el("n1", "select")
        doc = _doc(el)
        engine = LayoutEngine()
        a = engine.assess(el, doc)
        assert a.interaction_rank > 0.5
