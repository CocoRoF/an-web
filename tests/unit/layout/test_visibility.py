"""Unit tests for layout-lite visibility engine."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element
from an_web.layout.visibility import compute_visibility, is_offscreen


def _el(tag="div", attrs=None):
    return Element(node_id="n1", tag=tag, attributes=attrs or {})


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

    def test_opacity_zero(self):
        el = _el(attrs={"style": "opacity: 0"})
        assert compute_visibility(el) == "hidden"

    def test_aria_hidden_true(self):
        el = _el(attrs={"aria-hidden": "true"})
        assert compute_visibility(el) == "hidden"

    def test_aria_hidden_false(self):
        el = _el(attrs={"aria-hidden": "false"})
        assert compute_visibility(el) == "visible"

    def test_opacity_nonzero(self):
        el = _el(attrs={"style": "opacity: 0.5"})
        assert compute_visibility(el) == "visible"

    def test_input_hidden_type(self):
        el = _el(tag="input", attrs={"type": "hidden"})
        assert compute_visibility(el) == "none"


class TestIsOffscreen:
    def test_normal_element_not_offscreen(self):
        el = _el(attrs={"style": "position: absolute; left: 10px; top: 10px"})
        assert is_offscreen(el) is False

    def test_extreme_left_offscreen(self):
        el = _el(attrs={"style": "position: absolute; left: -9999px"})
        assert is_offscreen(el) is True

    def test_no_position(self):
        el = _el()
        assert is_offscreen(el) is False
