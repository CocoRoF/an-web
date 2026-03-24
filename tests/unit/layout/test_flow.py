"""Unit tests for layout-lite flow engine (display type, bbox, z-order)."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element, Document
from an_web.layout.flow import (
    get_display_type,
    compute_z_order,
    creates_stacking_context,
    infer_bbox_hint,
    compute_layout_info,
    compute_document_layout,
    FlowContext,
    LayoutInfo,
    BLOCK_TAGS,
    INLINE_TAGS,
)


def _el(tag="div", attrs=None):
    return Element(node_id="n1", tag=tag, attributes=attrs or {})


def _doc(*elements: Element) -> Document:
    doc = Document(url="https://example.com")
    for el in elements:
        doc.append_child(el)
    return doc


# ─── get_display_type ─────────────────────────────────────────────────────────

class TestGetDisplayType:
    def test_block_from_tag(self):
        assert get_display_type(_el("div")) == "block"
        assert get_display_type(_el("p")) == "block"
        assert get_display_type(_el("section")) == "block"
        assert get_display_type(_el("h1")) == "block"

    def test_inline_from_tag(self):
        assert get_display_type(_el("span")) == "inline"
        assert get_display_type(_el("a")) == "inline"
        assert get_display_type(_el("strong")) == "inline"

    def test_inline_block_from_tag(self):
        assert get_display_type(_el("button")) == "inline-block"
        assert get_display_type(_el("select")) == "inline-block"
        assert get_display_type(_el("textarea")) == "inline-block"

    def test_input_inline_block(self):
        el = _el("input", attrs={"type": "text"})
        assert get_display_type(el) == "inline-block"

    def test_input_hidden_is_none(self):
        el = _el("input", attrs={"type": "hidden"})
        assert get_display_type(el) == "none"

    def test_inline_style_overrides_tag(self):
        el = _el("div", attrs={"style": "display: flex"})
        assert get_display_type(el) == "flex"

    def test_inline_style_grid(self):
        el = _el("div", attrs={"style": "display: grid"})
        assert get_display_type(el) == "grid"

    def test_inline_style_none(self):
        el = _el("button", attrs={"style": "display: none"})
        assert get_display_type(el) == "none"

    def test_inline_style_inline_block(self):
        el = _el("div", attrs={"style": "display: inline-block"})
        assert get_display_type(el) == "inline-block"

    def test_unknown_tag(self):
        assert get_display_type(_el("my-custom-element")) == "unknown"

    def test_all_block_tags(self):
        for tag in BLOCK_TAGS:
            el = _el(tag)
            assert get_display_type(el) == "block", f"Expected block for <{tag}>"

    def test_all_inline_tags(self):
        for tag in INLINE_TAGS:
            el = _el(tag)
            assert get_display_type(el) == "inline", f"Expected inline for <{tag}>"


# ─── compute_z_order ─────────────────────────────────────────────────────────

class TestComputeZOrder:
    def test_default_z_order(self):
        el = _el()
        assert compute_z_order(el) == 0

    def test_explicit_z_index(self):
        el = _el(attrs={"style": "z-index: 100"})
        assert compute_z_order(el) == 100

    def test_negative_z_index(self):
        el = _el(attrs={"style": "z-index: -1"})
        assert compute_z_order(el) == -1

    def test_position_fixed_bonus(self):
        el = _el(attrs={"style": "position: fixed"})
        z = compute_z_order(el)
        assert z > 0

    def test_position_absolute_bonus(self):
        el = _el(attrs={"style": "position: absolute"})
        z = compute_z_order(el)
        assert z > 0

    def test_dialog_role_high_z(self):
        el = _el(attrs={"role": "dialog"})
        z = compute_z_order(el)
        assert z >= 100

    def test_alertdialog_higher_than_dialog(self):
        d = _el(attrs={"role": "dialog"})
        a = _el(attrs={"role": "alertdialog"})
        assert compute_z_order(a) > compute_z_order(d)

    def test_explicit_overrides_bonus(self):
        """Explicit z-index wins over positional bonus."""
        el = _el(attrs={"style": "position: fixed; z-index: 5"})
        assert compute_z_order(el) == 5

    def test_z_index_auto_no_override(self):
        el = _el(attrs={"style": "z-index: auto"})
        # auto → no explicit value, use position bonus only
        assert compute_z_order(el) == 0

    def test_dialog_tag(self):
        el = _el(tag="dialog")
        z = compute_z_order(el)
        assert z >= 100

    def test_tooltip_role(self):
        el = _el(attrs={"role": "tooltip"})
        z = compute_z_order(el)
        assert z > 0

    def test_menu_role(self):
        el = _el(attrs={"role": "menu"})
        z = compute_z_order(el)
        assert z > 0


# ─── creates_stacking_context ─────────────────────────────────────────────────

class TestCreatesStackingContext:
    def test_default_false(self):
        el = _el()
        assert creates_stacking_context(el) is False

    def test_position_absolute_with_z_index(self):
        el = _el(attrs={"style": "position: absolute; z-index: 1"})
        assert creates_stacking_context(el) is True

    def test_position_absolute_no_z_index(self):
        el = _el(attrs={"style": "position: absolute"})
        # No z-index → no stacking context
        assert creates_stacking_context(el) is False

    def test_opacity_creates_sc(self):
        el = _el(attrs={"style": "opacity: 0.5"})
        assert creates_stacking_context(el) is True

    def test_opacity_1_no_sc(self):
        el = _el(attrs={"style": "opacity: 1"})
        assert creates_stacking_context(el) is False

    def test_transform_creates_sc(self):
        el = _el(attrs={"style": "transform: rotate(45deg)"})
        assert creates_stacking_context(el) is True

    def test_filter_creates_sc(self):
        el = _el(attrs={"style": "filter: blur(2px)"})
        assert creates_stacking_context(el) is True

    def test_isolation_isolate(self):
        el = _el(attrs={"style": "isolation: isolate"})
        assert creates_stacking_context(el) is True

    def test_position_fixed_creates_sc(self):
        el = _el(attrs={"style": "position: fixed"})
        assert creates_stacking_context(el) is True

    def test_dialog_tag_creates_sc(self):
        el = _el(tag="dialog")
        assert creates_stacking_context(el) is True


# ─── infer_bbox_hint ─────────────────────────────────────────────────────────

class TestInferBboxHint:
    def test_button_typical_size(self):
        el = _el("button")
        x, y, w, h = infer_bbox_hint(el)
        assert w == 100
        assert h == 36
        assert x == 0

    def test_input_text_typical_size(self):
        el = _el("input", attrs={"type": "text"})
        _, _, w, h = infer_bbox_hint(el)
        assert w == 220
        assert h == 32

    def test_input_checkbox_small(self):
        el = _el("input", attrs={"type": "checkbox"})
        _, _, w, h = infer_bbox_hint(el)
        assert w == 16
        assert h == 16

    def test_input_hidden_zero_size(self):
        el = _el("input", attrs={"type": "hidden"})
        _, _, w, h = infer_bbox_hint(el)
        assert w == 0
        assert h == 0

    def test_textarea_typical_size(self):
        el = _el("textarea")
        _, _, w, h = infer_bbox_hint(el)
        assert w == 320
        assert h == 80

    def test_h1_full_width(self):
        el = _el("h1")
        _, _, w, h = infer_bbox_hint(el)
        assert w == 800
        assert h == 48

    def test_inline_style_overrides_width(self):
        el = _el("button", attrs={"style": "width: 200px"})
        _, _, w, h = infer_bbox_hint(el)
        assert w == 200

    def test_inline_style_overrides_both(self):
        el = _el("div", attrs={"style": "width: 400px; height: 100px"})
        _, _, w, h = infer_bbox_hint(el)
        assert w == 400
        assert h == 100

    def test_block_div_default(self):
        el = _el("div")
        _, _, w, h = infer_bbox_hint(el)
        assert w == 800  # full width

    def test_unknown_tag_fallback(self):
        el = _el("my-element")
        _, _, w, h = infer_bbox_hint(el)
        assert w > 0
        assert h > 0

    def test_flow_ctx_advances_y(self):
        ctx = FlowContext()
        el1 = _el("button")
        el2 = _el("input", attrs={"type": "text"})

        _, y1, _, h1 = infer_bbox_hint(el1, ctx)
        _, y2, _, h2 = infer_bbox_hint(el2, ctx)

        assert y1 == 0
        assert y2 == h1  # y2 should be h1 since el1 advanced by h1

    def test_img_typical_size(self):
        el = _el("img")
        _, _, w, h = infer_bbox_hint(el)
        assert w == 300
        assert h == 200


# ─── compute_layout_info ─────────────────────────────────────────────────────

class TestComputeLayoutInfo:
    def test_returns_layout_info(self):
        el = _el("button")
        info = compute_layout_info(el)
        assert isinstance(info, LayoutInfo)
        assert info.display_type == "inline-block"
        assert info.bbox_hint is not None
        assert info.z_order_hint >= 0

    def test_block_element(self):
        el = _el("div")
        info = compute_layout_info(el)
        assert info.display_type == "block"

    def test_stacking_context_flag(self):
        el = _el(attrs={"style": "position: fixed"})
        info = compute_layout_info(el)
        assert info.creates_stacking_context is True

    def test_normal_no_stacking_context(self):
        el = _el()
        info = compute_layout_info(el)
        assert info.creates_stacking_context is False


# ─── FlowContext ──────────────────────────────────────────────────────────────

class TestFlowContext:
    def test_initial_y_zero(self):
        ctx = FlowContext()
        assert ctx.y == 0

    def test_advance_returns_before_value(self):
        ctx = FlowContext()
        prev = ctx.advance(100)
        assert prev == 0
        assert ctx.y == 100

    def test_multiple_advances(self):
        ctx = FlowContext()
        ctx.advance(50)
        ctx.advance(30)
        assert ctx.y == 80

    def test_depth_tracking(self):
        ctx = FlowContext()
        assert ctx.depth == 0
        ctx.enter()
        assert ctx.depth == 1
        ctx.leave()
        assert ctx.depth == 0

    def test_depth_not_negative(self):
        ctx = FlowContext()
        ctx.leave()  # leave without enter
        assert ctx.depth == 0


# ─── compute_document_layout ─────────────────────────────────────────────────

class TestComputeDocumentLayout:
    def test_returns_dict_keyed_by_node_id(self):
        btn = Element(node_id="btn1", tag="button", attributes={})
        inp = Element(node_id="inp1", tag="input", attributes={"type": "text"})
        doc = Document(url="https://example.com")
        doc.append_child(btn)
        doc.append_child(inp)

        layout = compute_document_layout(doc)
        assert "btn1" in layout
        assert "inp1" in layout

    def test_y_positions_are_sequential(self):
        el1 = Element(node_id="b1", tag="h1", attributes={})
        el2 = Element(node_id="b2", tag="p", attributes={})
        doc = Document(url="https://example.com")
        doc.append_child(el1)
        doc.append_child(el2)

        layout = compute_document_layout(doc)
        y1 = layout["b1"].bbox_hint[1]
        y2 = layout["b2"].bbox_hint[1]
        # el2 should come after el1 in document order
        assert y2 > y1

    def test_nested_elements_included(self):
        parent = Element(node_id="p1", tag="div", attributes={})
        child = Element(node_id="c1", tag="button", attributes={})
        parent.append_child(child)
        doc = Document(url="https://example.com")
        doc.append_child(parent)

        layout = compute_document_layout(doc)
        assert "p1" in layout
        assert "c1" in layout
