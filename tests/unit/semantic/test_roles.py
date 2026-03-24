"""Unit tests for ARIA role inference."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element
from an_web.semantic.roles import (
    get_affordances, infer_role, is_interactive_role, is_structural_role,
    is_content_role, is_landmark_role, get_heading_level,
    WIDGET_ROLES, LANDMARK_ROLES, CONTENT_ROLES,
)


def _el(tag: str, attrs: dict | None = None) -> Element:
    return Element(node_id="n1", tag=tag, attributes=attrs or {})


class TestInferRole:
    def test_button(self):
        assert infer_role(_el("button")) == "button"

    def test_link(self):
        assert infer_role(_el("a")) == "link"

    def test_input_text(self):
        assert infer_role(_el("input", {"type": "text"})) == "textbox"

    def test_input_email(self):
        assert infer_role(_el("input", {"type": "email"})) == "textbox"

    def test_input_password(self):
        assert infer_role(_el("input", {"type": "password"})) == "textbox"

    def test_input_submit(self):
        assert infer_role(_el("input", {"type": "submit"})) == "button"

    def test_input_checkbox(self):
        assert infer_role(_el("input", {"type": "checkbox"})) == "checkbox"

    def test_input_radio(self):
        assert infer_role(_el("input", {"type": "radio"})) == "radio"

    def test_input_default_type(self):
        assert infer_role(_el("input")) == "textbox"

    def test_select(self):
        assert infer_role(_el("select")) == "combobox"

    def test_textarea(self):
        assert infer_role(_el("textarea")) == "textbox"

    def test_heading(self):
        for h in ("h1", "h2", "h3", "h4", "h5", "h6"):
            assert infer_role(_el(h)) == "heading"

    def test_explicit_aria_role_wins(self):
        el = _el("div", {"role": "button"})
        assert infer_role(el) == "button"

    def test_nav(self):
        assert infer_role(_el("nav")) == "navigation"

    def test_dialog(self):
        assert infer_role(_el("dialog")) == "dialog"

    def test_unknown_tag(self):
        assert infer_role(_el("custom-element")) == "generic"


class TestAffordances:
    def test_button_click(self):
        el = _el("button")
        aff = get_affordances("button", el)
        assert "click" in aff

    def test_textbox_type_clear(self):
        el = _el("input", {"type": "text"})
        aff = get_affordances("textbox", el)
        assert "type" in aff
        assert "clear" in aff

    def test_combobox_select(self):
        el = _el("select")
        aff = get_affordances("combobox", el)
        assert "select" in aff

    def test_link_click(self):
        el = _el("a")
        aff = get_affordances("link", el)
        assert "click" in aff

    def test_checkbox_click(self):
        el = _el("input", {"type": "checkbox"})
        aff = get_affordances("checkbox", el)
        assert "click" in aff


class TestRoleClassification:
    def test_interactive_roles(self):
        for role in ("button", "link", "textbox", "combobox", "checkbox"):
            assert is_interactive_role(role) is True

    def test_non_interactive_roles(self):
        for role in ("none", "generic", "banner", "navigation"):
            assert is_interactive_role(role) is False

    def test_structural_roles(self):
        for role in ("none", "generic", "list", "table", "banner"):
            assert is_structural_role(role) is True

    def test_non_structural_roles(self):
        for role in ("button", "link", "textbox"):
            assert is_structural_role(role) is False

    def test_content_roles(self):
        for role in ("heading", "img", "listitem", "article"):
            assert is_content_role(role) is True

    def test_non_content_roles(self):
        for role in ("button", "navigation", "generic"):
            assert is_content_role(role) is False

    def test_landmark_roles(self):
        for role in ("banner", "navigation", "main", "contentinfo"):
            assert is_landmark_role(role) is True

    def test_non_landmark_roles(self):
        for role in ("button", "heading", "generic", "none"):
            assert is_landmark_role(role) is False

    def test_widget_roles(self):
        for role in ("button", "checkbox", "combobox", "slider", "textbox"):
            assert role in WIDGET_ROLES


# ── get_heading_level ──────────────────────────────────────────────────────────

class TestGetHeadingLevel:
    def test_h1_returns_1(self):
        assert get_heading_level(_el("h1")) == 1

    def test_h2_returns_2(self):
        assert get_heading_level(_el("h2")) == 2

    def test_h6_returns_6(self):
        assert get_heading_level(_el("h6")) == 6

    def test_non_heading_returns_none(self):
        assert get_heading_level(_el("div")) is None
        assert get_heading_level(_el("p")) is None
        assert get_heading_level(_el("button")) is None

    def test_aria_level_override(self):
        el = _el("div", {"role": "heading", "aria-level": "3"})
        # aria-level=3 on a non-h3 tag — heading level should return 3
        assert get_heading_level(el) == 3

    def test_aria_level_invalid_ignored(self):
        el = _el("h2", {"aria-level": "bad"})
        # Invalid aria-level → fall back to tag
        assert get_heading_level(el) == 2

    def test_aria_level_out_of_range_ignored(self):
        el = _el("h2", {"aria-level": "10"})
        # Out of range (>6) → fall back to tag
        assert get_heading_level(el) == 2


# ── Extended affordances ───────────────────────────────────────────────────────

class TestExtendedAffordances:
    def test_disabled_element_no_affordances(self):
        el = _el("button", {"disabled": ""})
        aff = get_affordances("button", el)
        assert aff == []

    def test_readonly_textbox_no_type_clear(self):
        el = _el("input", {"type": "text", "readonly": ""})
        aff = get_affordances("textbox", el)
        assert "type" not in aff
        assert "clear" not in aff
        assert "focus" in aff

    def test_unchecked_checkbox_has_check(self):
        el = _el("input", {"type": "checkbox"})
        aff = get_affordances("checkbox", el)
        assert "check" in aff
        assert "uncheck" not in aff

    def test_checked_checkbox_has_uncheck(self):
        el = _el("input", {"type": "checkbox", "checked": ""})
        aff = get_affordances("checkbox", el)
        assert "uncheck" in aff
        assert "check" not in aff

    def test_radio_has_check(self):
        el = _el("input", {"type": "radio"})
        aff = get_affordances("radio", el)
        assert "check" in aff
        assert "click" in aff

    def test_submit_button_has_submit(self):
        el = _el("button", {"type": "submit"})
        aff = get_affordances("button", el)
        assert "submit" in aff

    def test_submit_input_has_submit(self):
        el = _el("input", {"type": "submit"})
        aff = get_affordances("button", el)
        assert "submit" in aff

    def test_combobox_has_select_and_click(self):
        el = _el("select")
        aff = get_affordances("combobox", el)
        assert "select" in aff
        assert "click" in aff

    def test_searchbox_has_type_and_clear(self):
        el = _el("input", {"type": "search"})
        aff = get_affordances("searchbox", el)
        assert "type" in aff
        assert "clear" in aff
        assert "focus" in aff

    def test_slider_has_click_and_type(self):
        el = _el("input", {"type": "range"})
        aff = get_affordances("slider", el)
        assert "click" in aff
        assert "type" in aff

    def test_overflow_element_has_scroll(self):
        el = _el("div", {"style": "overflow: auto; height: 200px"})
        aff = get_affordances("generic", el)
        assert "scroll" in aff

    def test_summary_has_click(self):
        el = _el("summary")
        aff = get_affordances("summary", el)
        assert "click" in aff

    def test_tab_has_click(self):
        el = _el("div", {"role": "tab"})
        aff = get_affordances("tab", el)
        assert "click" in aff
