"""Unit tests for layout-lite visibility engine."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element
from an_web.layout.visibility import (
    compute_visibility,
    compute_visibility_result,
    compute_visibility_cascaded,
    is_visible,
    is_offscreen,
    VisibilityResult,
    _parse_inline_style,
    _parse_px,
)


def _el(tag="div", attrs=None):
    return Element(node_id="n1", tag=tag, attributes=attrs or {})


# ─── compute_visibility (single-element) ─────────────────────────────────────

class TestComputeVisibility:
    def test_default_visible(self):
        el = _el()
        assert compute_visibility(el) == "visible"

    def test_hidden_attribute(self):
        el = _el(attrs={"hidden": ""})
        assert compute_visibility(el) == "none"

    def test_display_none_inline_style(self):
        el = _el(attrs={"style": "display: none"})
        assert compute_visibility(el) == "none"

    def test_visibility_hidden_inline_style(self):
        el = _el(attrs={"style": "visibility: hidden"})
        assert compute_visibility(el) == "hidden"

    def test_visibility_collapse(self):
        el = _el(attrs={"style": "visibility: collapse"})
        assert compute_visibility(el) == "hidden"

    def test_opacity_zero(self):
        el = _el(attrs={"style": "opacity: 0"})
        assert compute_visibility(el) == "hidden"

    def test_opacity_zero_float(self):
        el = _el(attrs={"style": "opacity: 0.0"})
        assert compute_visibility(el) == "hidden"

    def test_opacity_nonzero(self):
        el = _el(attrs={"style": "opacity: 0.5"})
        assert compute_visibility(el) == "visible"

    def test_aria_hidden_true(self):
        el = _el(attrs={"aria-hidden": "true"})
        assert compute_visibility(el) == "hidden"

    def test_aria_hidden_false(self):
        el = _el(attrs={"aria-hidden": "false"})
        assert compute_visibility(el) == "visible"

    def test_input_hidden_type(self):
        el = _el(tag="input", attrs={"type": "hidden"})
        assert compute_visibility(el) == "none"

    def test_width_and_height_zero(self):
        el = _el(attrs={"style": "width: 0px; height: 0px"})
        assert compute_visibility(el) == "hidden"

    def test_width_zero_height_nonzero(self):
        el = _el(attrs={"style": "width: 0px; height: 20px"})
        # Only BOTH zero triggers hidden
        assert compute_visibility(el) == "visible"

    def test_clip_rect_zero(self):
        """clip:rect(0,0,0,0) with position:absolute;overflow:hidden → hidden."""
        el = _el(attrs={"style": "position: absolute; overflow: hidden; clip: rect(0,0,0,0)"})
        assert compute_visibility(el) == "hidden"

    def test_off_screen_absolute(self):
        el = _el(attrs={"style": "position: absolute; left: -10000px"})
        assert compute_visibility(el) == "hidden"


# ─── VisibilityResult ─────────────────────────────────────────────────────────

class TestVisibilityResult:
    def test_visible_state(self):
        r = VisibilityResult(state="visible", reason="default")
        assert r.is_visible is True
        assert r.is_none is False
        assert r.is_hidden is False

    def test_none_state(self):
        r = VisibilityResult(state="none", reason="display:none")
        assert r.is_none is True
        assert r.is_visible is False

    def test_hidden_state(self):
        r = VisibilityResult(state="hidden", reason="visibility:hidden")
        assert r.is_hidden is True
        assert r.is_visible is False

    def test_cascaded_flag(self):
        r = VisibilityResult(state="none", reason="ancestor", cascaded=True)
        assert r.cascaded is True
        assert "inherited" in str(r)

    def test_str_representation(self):
        r = VisibilityResult(state="visible", reason="default")
        s = str(r)
        assert "visible" in s

    def test_reason_provided(self):
        el = _el(attrs={"style": "display: none"})
        result = compute_visibility_result(el)
        assert "display:none" in result.reason

    def test_reason_for_input_hidden(self):
        el = _el(tag="input", attrs={"type": "hidden"})
        result = compute_visibility_result(el)
        assert "hidden" in result.reason.lower()


# ─── Cascaded visibility ──────────────────────────────────────────────────────

class TestComputeVisibilityCascaded:
    def test_no_parent_returns_own(self):
        el = _el(attrs={"style": "display:none"})
        result = compute_visibility_cascaded(el)
        assert result.state == "none"
        assert result.cascaded is False

    def test_parent_display_none_hides_child(self):
        parent = Element(node_id="p1", tag="div", attributes={"style": "display:none"})
        child = Element(node_id="c1", tag="span", attributes={})
        parent.append_child(child)

        result = compute_visibility_cascaded(child)
        assert result.state == "none"
        assert result.cascaded is True

    def test_parent_visibility_hidden_inherits(self):
        parent = Element(node_id="p1", tag="div", attributes={"style": "visibility:hidden"})
        child = Element(node_id="c1", tag="span", attributes={})
        parent.append_child(child)

        result = compute_visibility_cascaded(child)
        assert result.state == "hidden"
        assert result.cascaded is True

    def test_child_can_override_visibility(self):
        """Child with visibility:visible overrides parent's visibility:hidden."""
        parent = Element(node_id="p1", tag="div", attributes={"style": "visibility:hidden"})
        child = Element(node_id="c1", tag="span", attributes={"style": "visibility:visible"})
        parent.append_child(child)

        result = compute_visibility_cascaded(child)
        # Child explicitly re-declares visible — it should be visible
        # (parent's visibility:hidden is not display:none — child can override)
        # Note: the current implementation checks if child has own visibility:visible
        assert result.state == "visible"

    def test_grandparent_display_none(self):
        gp = Element(node_id="gp1", tag="div", attributes={"style": "display:none"})
        parent = Element(node_id="p1", tag="div", attributes={})
        child = Element(node_id="c1", tag="span", attributes={})
        gp.append_child(parent)
        parent.append_child(child)

        result = compute_visibility_cascaded(child)
        assert result.state == "none"
        assert result.cascaded is True

    def test_visible_chain(self):
        parent = Element(node_id="p1", tag="div", attributes={})
        child = Element(node_id="c1", tag="span", attributes={})
        parent.append_child(child)

        result = compute_visibility_cascaded(child)
        assert result.state == "visible"
        assert result.cascaded is False


# ─── is_visible helper ────────────────────────────────────────────────────────

class TestIsVisible:
    def test_visible_element(self):
        el = _el()
        assert is_visible(el) is True

    def test_display_none_not_visible(self):
        el = _el(attrs={"style": "display:none"})
        assert is_visible(el) is False

    def test_hidden_attr_not_visible(self):
        el = _el(attrs={"hidden": ""})
        assert is_visible(el) is False

    def test_aria_hidden_not_visible(self):
        el = _el(attrs={"aria-hidden": "true"})
        assert is_visible(el) is False


# ─── is_offscreen ─────────────────────────────────────────────────────────────

class TestIsOffscreen:
    def test_normal_element_not_offscreen(self):
        el = _el(attrs={"style": "position: absolute; left: 10px; top: 10px"})
        assert is_offscreen(el) is False

    def test_extreme_left_offscreen(self):
        el = _el(attrs={"style": "position: absolute; left: -9999px"})
        assert is_offscreen(el) is True

    def test_extreme_top_offscreen(self):
        el = _el(attrs={"style": "position: fixed; top: -9999px"})
        assert is_offscreen(el) is True

    def test_no_position(self):
        el = _el()
        assert is_offscreen(el) is False

    def test_extreme_left_no_position(self):
        # Without position:absolute/fixed, left is irrelevant
        el = _el(attrs={"style": "left: -9999px"})
        assert is_offscreen(el) is False

    def test_moderate_negative_left(self):
        el = _el(attrs={"style": "position: absolute; left: -100px"})
        assert is_offscreen(el) is False


# ─── _parse_inline_style ──────────────────────────────────────────────────────

class TestParseInlineStyle:
    def test_single_property(self):
        props = _parse_inline_style("display: none")
        assert props["display"] == "none"

    def test_multiple_properties(self):
        props = _parse_inline_style("display: flex; visibility: hidden; opacity: 0.5")
        assert props["display"] == "flex"
        assert props["visibility"] == "hidden"
        assert props["opacity"] == "0.5"

    def test_empty_string(self):
        assert _parse_inline_style("") == {}

    def test_trailing_semicolon(self):
        props = _parse_inline_style("color: red;")
        assert "color" in props

    def test_lowercase_keys(self):
        props = _parse_inline_style("DISPLAY: NONE")
        assert "display" in props
        assert props["display"] == "NONE"

    def test_value_with_colon(self):
        """Values containing colons (e.g., clip: rect(0,0,0,0)) should parse correctly."""
        props = _parse_inline_style("clip: rect(0,0,0,0)")
        assert "clip" in props

    def test_z_index(self):
        props = _parse_inline_style("z-index: 100")
        assert props["z-index"] == "100"


# ─── _parse_px ────────────────────────────────────────────────────────────────

class TestParsePx:
    def test_integer(self):
        assert _parse_px("100") == 100.0

    def test_px_suffix(self):
        assert _parse_px("100px") == 100.0

    def test_negative(self):
        assert _parse_px("-9999px") == -9999.0

    def test_float(self):
        assert _parse_px("1.5px") == 1.5

    def test_none_input(self):
        assert _parse_px(None) is None

    def test_empty(self):
        assert _parse_px("") is None

    def test_non_numeric(self):
        assert _parse_px("auto") is None

    def test_em_not_parseable(self):
        # We only strip 'px', not 'em'
        assert _parse_px("10em") is None
