"""Tests for an_web/js/bridge.py — JS<->Python marshalling."""
from __future__ import annotations

import json
import pytest
from an_web.js.bridge import (
    JSError,
    EvalResult,
    py_to_js,
    js_to_py,
    js_to_py_string,
    make_json_callable,
    marshal_element,
    marshal_document,
    _inner_html,
)


# ── JSError ──────────────────────────────────────────────────────────────────


class TestJSError:
    def test_basic_fields(self):
        err = JSError(message="test msg", js_type="TypeError", stack="at foo:1")
        assert err.message == "test msg"
        assert err.js_type == "TypeError"
        assert err.stack == "at foo:1"

    def test_str_with_stack(self):
        err = JSError(message="bad", js_type="RangeError", stack="    at x:1")
        s = str(err)
        assert "RangeError" in s
        assert "bad" in s
        assert "at x:1" in s

    def test_str_without_stack(self):
        err = JSError(message="simple")
        assert "Error" in str(err)
        assert "simple" in str(err)

    def test_from_quickjs_exception_type_prefix(self):
        class FakeExc(Exception):
            pass
        exc = FakeExc("TypeError: cannot read property\n    at <eval>:1")
        err = JSError.from_quickjs_exception(exc)
        assert err.js_type == "TypeError"
        assert "cannot read property" in err.message
        assert "at <eval>:1" in err.stack

    def test_from_quickjs_exception_no_prefix(self):
        class FakeExc(Exception):
            pass
        exc = FakeExc("plain error message")
        err = JSError.from_quickjs_exception(exc)
        assert err.js_type == "Error"
        assert err.message == "plain error message"

    def test_is_exception(self):
        err = JSError(message="oops")
        with pytest.raises(JSError):
            raise err


# ── EvalResult ───────────────────────────────────────────────────────────────


class TestEvalResult:
    def test_success(self):
        r = EvalResult.success(42)
        assert r.ok is True
        assert r.value == 42
        assert r.error is None

    def test_failure(self):
        err = JSError(message="fail")
        r = EvalResult.failure(err)
        assert r.ok is False
        assert r.error is err
        assert r.value is None

    def test_unwrap_success(self):
        r = EvalResult.success("hello")
        assert r.unwrap() == "hello"

    def test_unwrap_failure_raises(self):
        err = JSError(message="err")
        r = EvalResult.failure(err)
        with pytest.raises(JSError):
            r.unwrap()


# ── py_to_js ─────────────────────────────────────────────────────────────────


class TestPyToJs:
    def test_primitives_pass_through(self):
        assert py_to_js(None) is None
        assert py_to_js(True) is True
        assert py_to_js(42) == 42
        assert py_to_js(3.14) == pytest.approx(3.14)
        assert py_to_js("hello") == "hello"

    def test_dict_recursed(self):
        result = py_to_js({"a": 1, "b": [2, 3]})
        assert result == {"a": 1, "b": [2, 3]}

    def test_list_recursed(self):
        result = py_to_js([1, "x", True, None])
        assert result == [1, "x", True, None]

    def test_tuple_as_list(self):
        result = py_to_js((1, 2, 3))
        assert result == [1, 2, 3]

    def test_nested_structure(self):
        result = py_to_js({"key": [{"inner": True}]})
        assert result == {"key": [{"inner": True}]}

    def test_non_serialisable_falls_back_to_json_string(self):
        class Obj:
            def __repr__(self):
                return "ObjRepr"
        result = py_to_js(Obj())
        assert isinstance(result, str)


# ── js_to_py ─────────────────────────────────────────────────────────────────


class TestJsToPy:
    def test_primitives_pass_through(self):
        assert js_to_py(None) is None
        assert js_to_py(42) == 42
        assert js_to_py("hello") == "hello"
        assert js_to_py(True) is True

    def test_object_with_json_method(self):
        class JSObj:
            def json(self):
                return '{"x": 1, "y": [1,2,3]}'

        result = js_to_py(JSObj())
        assert result == {"x": 1, "y": [1, 2, 3]}

    def test_object_with_invalid_json_returns_raw(self):
        class JSObj:
            def json(self):
                return "not valid json }{{"

        result = js_to_py(JSObj())
        assert result == "not valid json }{{"

    def test_object_json_raises_returns_value(self):
        class JSObj:
            def json(self):
                raise RuntimeError("broken")

        result = js_to_py(JSObj())
        assert result is JSObj or isinstance(result, JSObj) or result is not None

    def test_js_to_py_string_none(self):
        assert js_to_py_string(None) == ""

    def test_js_to_py_string_number(self):
        assert js_to_py_string(42) == "42"


# ── make_json_callable ────────────────────────────────────────────────────────


class TestMakeJsonCallable:
    def test_basic_return(self):
        def fn(x):
            return x * 2

        wrapped = make_json_callable(fn)
        result = json.loads(wrapped(json.dumps(5)))
        assert result == 10

    def test_dict_round_trip(self):
        def fn(data):
            return {"received": data["value"]}

        wrapped = make_json_callable(fn)
        result = json.loads(wrapped(json.dumps({"value": "hello"})))
        assert result == {"received": "hello"}

    def test_exception_returns_error_dict(self):
        def fn():
            raise ValueError("intentional")

        wrapped = make_json_callable(fn)
        result = json.loads(wrapped())
        assert "__error__" in result
        assert "intentional" in result["__error__"]

    def test_string_args_auto_decoded(self):
        def fn(a, b):
            return a + b

        wrapped = make_json_callable(fn)
        result = json.loads(wrapped(json.dumps(3), json.dumps(4)))
        assert result == 7

    def test_preserves_function_name(self):
        def my_special_fn(x):
            return x

        wrapped = make_json_callable(my_special_fn)
        assert wrapped.__name__ == "my_special_fn"

    def test_non_json_string_passed_as_is(self):
        def fn(s):
            return s.upper()

        wrapped = make_json_callable(fn)
        result = json.loads(wrapped("hello"))
        assert result == "HELLO"


# ── marshal_element ───────────────────────────────────────────────────────────


class TestMarshalElement:
    def _make_element(self, tag="div", attrs=None, text=""):
        from an_web.dom.nodes import Element, TextNode, NodeType
        el = Element(node_id="n1", tag=tag, attributes=attrs or {})
        if text:
            t = TextNode(node_id="t1", data=text)
            el.append_child(t)
        return el

    def test_none_returns_empty_dict(self):
        result = marshal_element(None)
        assert result == {}

    def test_basic_element(self):
        el = self._make_element("button", {"type": "submit", "id": "btn"})
        result = marshal_element(el)
        assert result["tag"] == "button"
        assert result["tagName"] == "BUTTON"
        assert result["id"] == "btn"
        assert result["nodeType"] == 1
        assert result["attributes"]["type"] == "submit"

    def test_text_content(self):
        el = self._make_element("p", {}, "Hello World")
        result = marshal_element(el)
        assert result["textContent"] == "Hello World"

    def test_children_included(self):
        from an_web.dom.nodes import Element, TextNode
        parent = Element(node_id="p1", tag="div")
        child = Element(node_id="c1", tag="span", attributes={"class": "item"})
        text = TextNode(node_id="t1", data="text")
        parent.append_child(child)
        parent.append_child(text)

        result = marshal_element(parent)
        assert len(result["children"]) == 2
        assert result["children"][0]["tag"] == "span"
        assert result["children"][0]["nodeType"] == 1
        assert result["children"][1]["tag"] == "#text"
        assert result["children"][1]["nodeType"] == 3

    def test_ai_fields_included(self):
        el = self._make_element("button")
        el.is_interactive = True
        el.visibility_state = "none"
        el.semantic_role = "button"
        result = marshal_element(el)
        assert result["isInteractive"] is True
        assert result["visibilityState"] == "none"
        assert result["semanticRole"] == "button"

    def test_inner_html(self):
        from an_web.dom.nodes import Element, TextNode
        parent = Element(node_id="p1", tag="div")
        child = Element(node_id="c1", tag="span", attributes={"class": "x"})
        text = TextNode(node_id="t1", data="hello")
        child.append_child(text)
        parent.append_child(child)

        html = _inner_html(parent)
        assert '<span class="x">hello</span>' == html


# ── marshal_document ─────────────────────────────────────────────────────────


class TestMarshalDocument:
    def test_empty_doc(self):
        result = marshal_document(None)
        assert result["title"] == ""
        assert result["url"] == "about:blank"
        assert result["nodeType"] == 9

    def test_real_doc(self):
        from an_web.browser.parser import parse_html
        doc = parse_html(
            "<html><head><title>Test Page</title></head><body></body></html>",
            "https://example.com/"
        )
        result = marshal_document(doc)
        assert result["title"] == "Test Page"
        assert result["url"] == "https://example.com/"
        assert result["readyState"] == "complete"
