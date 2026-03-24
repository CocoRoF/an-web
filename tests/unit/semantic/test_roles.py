"""Unit tests for ARIA role inference."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Element
from an_web.semantic.roles import (
    get_affordances, infer_role, is_interactive_role, is_structural_role,
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
