"""Unit tests for AI tool schema definitions."""
from __future__ import annotations

import pytest
from an_web.api.tool_schema import (
    TOOLS, TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI,
    get_tool, get_tool_names, get_schema,
)


EXPECTED_TOOLS = {
    "navigate", "snapshot", "click", "type", "clear",
    "select", "submit", "extract", "scroll", "wait_for", "eval_js",
}


class TestToolList:
    def test_all_tools_present(self):
        names = {t["name"] for t in TOOLS}
        assert EXPECTED_TOOLS == names

    def test_each_tool_has_required_keys(self):
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_descriptions_are_nonempty(self):
        for tool in TOOLS:
            assert len(tool["description"].strip()) > 10, f"{tool['name']} description too short"

    def test_input_schema_type_object(self):
        for tool in TOOLS:
            assert tool["input_schema"].get("type") == "object", f"{tool['name']} missing type:object"


class TestNavigateSchema:
    def test_url_required(self):
        s = get_schema("navigate")
        assert "url" in s["required"]

    def test_url_is_string(self):
        s = get_schema("navigate")
        assert s["properties"]["url"]["type"] == "string"


class TestClickSchema:
    def test_target_required(self):
        s = get_schema("click")
        assert "target" in s["required"]

    def test_target_oneOf(self):
        s = get_schema("click")
        assert "oneOf" in s["properties"]["target"]


class TestTypeSchema:
    def test_target_and_text_required(self):
        s = get_schema("type")
        assert "target" in s["required"]
        assert "text" in s["required"]

    def test_append_optional(self):
        s = get_schema("type")
        assert "append" in s["properties"]


class TestExtractSchema:
    def test_query_required(self):
        s = get_schema("extract")
        assert "query" in s["required"]

    def test_mode_enum(self):
        s = get_schema("extract")
        assert s["properties"]["mode"]["enum"] == ["css", "structured", "json", "html"]


class TestWaitForSchema:
    def test_condition_required(self):
        s = get_schema("wait_for")
        assert "condition" in s["required"]

    def test_condition_enum(self):
        s = get_schema("wait_for")
        assert set(s["properties"]["condition"]["enum"]) == {
            "network_idle", "dom_stable", "element_visible"
        }


class TestToolsForClaude:
    def test_count_matches(self):
        assert len(TOOLS_FOR_CLAUDE) == len(TOOLS)

    def test_each_has_input_schema_key(self):
        for tool in TOOLS_FOR_CLAUDE:
            assert "input_schema" in tool
            assert "name" in tool
            assert "description" in tool

    def test_no_parameters_key(self):
        for tool in TOOLS_FOR_CLAUDE:
            assert "parameters" not in tool


class TestToolsForOpenAI:
    def test_count_matches(self):
        assert len(TOOLS_FOR_OPENAI) == len(TOOLS)

    def test_type_is_function(self):
        for tool in TOOLS_FOR_OPENAI:
            assert tool["type"] == "function"

    def test_has_function_wrapper(self):
        for tool in TOOLS_FOR_OPENAI:
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_no_input_schema_key(self):
        for tool in TOOLS_FOR_OPENAI:
            assert "input_schema" not in tool["function"]


class TestLookupHelpers:
    def test_get_tool_found(self):
        t = get_tool("navigate")
        assert t is not None
        assert t["name"] == "navigate"

    def test_get_tool_not_found(self):
        assert get_tool("nonexistent") is None

    def test_get_tool_names(self):
        names = get_tool_names()
        assert isinstance(names, list)
        assert "navigate" in names
        assert len(names) == len(TOOLS)

    def test_get_schema(self):
        s = get_schema("snapshot")
        assert isinstance(s, dict)
        assert s["type"] == "object"

    def test_get_schema_not_found(self):
        assert get_schema("nope") is None
