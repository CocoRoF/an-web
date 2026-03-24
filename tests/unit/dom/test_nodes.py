"""Unit tests for DOM node model."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Document, Element, Node, NodeType, TextNode


class TestElement:
    def _el(self, tag="div", attrs=None, node_id="n1"):
        return Element(node_id=node_id, tag=tag, attributes=attrs or {})

    def test_tag_normalized_lowercase(self):
        el = self._el(tag="BUTTON")
        assert el.tag == "button"

    def test_get_attribute(self):
        el = self._el(attrs={"id": "main", "class": "foo bar"})
        assert el.get_attribute("id") == "main"
        assert el.get_attribute("missing") is None

    def test_set_attribute(self):
        el = self._el()
        el.set_attribute("data-test", "value")
        assert el.get_attribute("data-test") == "value"

    def test_has_attribute(self):
        el = self._el(attrs={"disabled": ""})
        assert el.has_attribute("disabled") is True
        assert el.has_attribute("hidden") is False

    def test_get_id(self):
        el = self._el(attrs={"id": "submit-btn"})
        assert el.get_id() == "submit-btn"

    def test_get_class_list(self):
        el = self._el(attrs={"class": "btn btn-primary active"})
        assert el.get_class_list() == ["btn", "btn-primary", "active"]

    def test_get_class_list_empty(self):
        el = self._el()
        assert el.get_class_list() == []

    def test_is_disabled(self):
        el = self._el(attrs={"disabled": ""})
        assert el.is_disabled() is True
        el2 = self._el()
        assert el2.is_disabled() is False

    def test_is_hidden_attr(self):
        el = self._el(attrs={"hidden": ""})
        assert el.is_hidden() is True

    def test_is_hidden_input_type(self):
        el = self._el(tag="input", attrs={"type": "hidden"})
        assert el.is_hidden() is True

    def test_append_child(self):
        parent = self._el(node_id="p")
        child = self._el(node_id="c")
        parent.append_child(child)
        assert child in parent.children
        assert child.parent is parent

    def test_remove_child(self):
        parent = self._el(node_id="p")
        child = self._el(node_id="c")
        parent.append_child(child)
        parent.remove_child(child)
        assert child not in parent.children
        assert child.parent is None

    def test_text_content_from_text_nodes(self):
        parent = self._el()
        parent.append_child(TextNode(node_id="t1", data="Hello "))
        parent.append_child(TextNode(node_id="t2", data="World"))
        assert parent.text_content == "Hello World"

    def test_iter_descendants(self):
        root = self._el(node_id="root")
        child1 = self._el(node_id="c1")
        child2 = self._el(node_id="c2")
        grandchild = self._el(node_id="gc")
        root.append_child(child1)
        root.append_child(child2)
        child1.append_child(grandchild)
        desc = list(root.iter_descendants())
        ids = [n.node_id for n in desc]
        assert "c1" in ids
        assert "c2" in ids
        assert "gc" in ids
        assert "root" not in ids

    def test_to_dict(self):
        el = self._el(tag="button", attrs={"class": "btn"})
        el.semantic_role = "button"
        el.is_interactive = True
        d = el.to_dict()
        assert d["tag"] == "button"
        assert d["semantic_role"] == "button"
        assert d["is_interactive"] is True


class TestDocument:
    def test_get_element_by_id(self):
        doc = Document()
        el = Element(node_id="n1", tag="div", attributes={"id": "main"})
        doc.register_element(el)
        assert doc.get_element_by_id("main") is el
        assert doc.get_element_by_id("missing") is None

    def test_body_property(self):
        doc = Document()
        body = Element(node_id="body", tag="body")
        doc.append_child(body)
        assert doc.body is body

    def test_iter_elements(self):
        doc = Document()
        div = Element(node_id="d1", tag="div")
        span = Element(node_id="s1", tag="span")
        doc.append_child(div)
        div.append_child(span)
        elements = list(doc.iter_elements())
        tags = {e.tag for e in elements}
        assert "div" in tags
        assert "span" in tags


class TestTextNode:
    def test_creation(self):
        t = TextNode(node_id="t1", data="Hello World")
        assert t.data == "Hello World"
        assert t.node_type == NodeType.TEXT

    def test_whole_text(self):
        t = TextNode(node_id="t1", data="Test")
        assert t.whole_text == "Test"
