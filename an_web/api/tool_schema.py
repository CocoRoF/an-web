"""
AI Tool schema definitions — JSON Schema compatible.

These schemas are designed to be passed directly to AI models
(Claude, GPT-4, etc.) as tool definitions.

Usage::

    from an_web.api.tool_schema import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI
    from an_web.api.tool_schema import get_tool, get_tool_names, TOOLS

Formats:
    TOOLS_FOR_CLAUDE  — Anthropic ``tools`` parameter (input_schema key)
    TOOLS_FOR_OPENAI  — OpenAI ``tools`` parameter (parameters key + function wrapper)
"""
from __future__ import annotations

from typing import Any


# ── Shared sub-schemas ─────────────────────────────────────────────────────────

def _target_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {
                "type": "string",
                "description": "CSS selector (e.g. '#login-btn') or XPath",
            },
            {
                "type": "object",
                "description": "Semantic target specification",
                "properties": {
                    "by": {
                        "type": "string",
                        "enum": ["semantic", "role", "text", "node_id"],
                        "description": (
                            "Targeting method: 'role'+text for ARIA-based, "
                            "'text' for visible text match, 'node_id' for exact ID"
                        ),
                    },
                    "role": {"type": "string", "description": "ARIA role (e.g. 'button', 'link', 'textbox')"},
                    "text": {"type": "string", "description": "Accessible name or visible text (partial match)"},
                    "node_id": {"type": "string", "description": "Internal node_id from snapshot()"},
                    "name": {"type": "string", "description": "Accessible name filter (for disambiguation)"},
                },
                "required": ["by"],
            },
        ]
    }


def _optional_target_schema(description: str = "") -> dict[str, Any]:
    schema = _target_schema().copy()
    schema["description"] = description
    return schema


# ── Tool definitions ───────────────────────────────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    # ── navigate ──────────────────────────────────────────────────────────────
    {
        "name": "navigate",
        "description": (
            "Navigate to a URL and wait for the page to load. "
            "Returns action result with final URL and load status. "
            "Always follow with snapshot() to understand the new page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute URL to navigate to (e.g. 'https://example.com')",
                },
            },
            "required": ["url"],
        },
    },

    # ── snapshot ──────────────────────────────────────────────────────────────
    {
        "name": "snapshot",
        "description": (
            "Get the current page's semantic state as a structured world model. "
            "Returns page type, primary actions, input fields, and full semantic tree. "
            "Use this after every navigation and after major DOM changes to understand "
            "what's on the page before acting. "
            "Prefer this over eval_js for page inspection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },

    # ── click ─────────────────────────────────────────────────────────────────
    {
        "name": "click",
        "description": (
            "Click an element by dispatching MouseEvent(s). "
            "Prefer semantic targeting (role+text) over CSS selectors. "
            "After clicking, observe effects: navigation, modal, DOM changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },

    # ── type ──────────────────────────────────────────────────────────────────
    {
        "name": "type",
        "description": (
            "Type text into an input field, textarea, or contenteditable element. "
            "Dispatches InputEvent and change events. "
            "Use 'append': true to add to existing value instead of replacing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
                "text": {
                    "type": "string",
                    "description": "Text to type into the field",
                },
                "append": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, append to existing value; if false, replace",
                },
            },
            "required": ["target", "text"],
        },
    },

    # ── clear ─────────────────────────────────────────────────────────────────
    {
        "name": "clear",
        "description": "Clear the value of an input field or textarea.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },

    # ── select ────────────────────────────────────────────────────────────────
    {
        "name": "select",
        "description": (
            "Select an option from a <select> dropdown. "
            "Use 'by_text': true to select by visible text instead of option value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
                "value": {
                    "type": "string",
                    "description": "Option value (or visible text if by_text=true) to select",
                },
                "by_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "Match by visible option text instead of value attribute",
                },
            },
            "required": ["target", "value"],
        },
    },

    # ── submit ────────────────────────────────────────────────────────────────
    {
        "name": "submit",
        "description": (
            "Submit a form. Target can be the form element itself or any element inside it. "
            "Triggers form submission events and waits for navigation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },

    # ── extract ───────────────────────────────────────────────────────────────
    {
        "name": "extract",
        "description": (
            "Extract structured data from the page. "
            "mode='css': list of matching elements (text + attributes). "
            "mode='structured': full structured extraction. "
            "mode='html': raw HTML of matched elements. "
            "mode='json': JSON value from data attributes or JSON-LD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "CSS selector (e.g. 'table tr', 'a[href]', '.product-price')",
                },
                "mode": {
                    "type": "string",
                    "enum": ["css", "structured", "json", "html"],
                    "default": "css",
                    "description": "Extraction mode",
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum number of results to return",
                },
            },
            "required": ["query"],
        },
    },

    # ── scroll ────────────────────────────────────────────────────────────────
    {
        "name": "scroll",
        "description": (
            "Scroll the page or a specific element. "
            "Omit 'target' to scroll the entire page. "
            "delta_y positive = scroll down; negative = scroll up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    **_target_schema(),
                    "description": "Element to scroll; omit to scroll the page",
                },
                "delta_x": {
                    "type": "integer",
                    "default": 0,
                    "description": "Horizontal scroll delta in pixels",
                },
                "delta_y": {
                    "type": "integer",
                    "default": 300,
                    "description": "Vertical scroll delta in pixels (positive = down)",
                },
            },
        },
    },

    # ── wait_for ──────────────────────────────────────────────────────────────
    {
        "name": "wait_for",
        "description": (
            "Wait until a condition is satisfied before proceeding. "
            "'network_idle': wait for all pending requests to complete. "
            "'dom_stable': wait for DOM mutations to stop. "
            "'element_visible': wait for a CSS-selected element to become visible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "enum": ["network_idle", "dom_stable", "element_visible"],
                    "description": "Condition to wait for",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector — required for condition='element_visible'",
                },
                "timeout_ms": {
                    "type": "integer",
                    "default": 5000,
                    "description": "Maximum wait time in milliseconds before failing",
                },
            },
            "required": ["condition"],
        },
    },

    # ── eval_js ───────────────────────────────────────────────────────────────
    {
        "name": "eval_js",
        "description": (
            "Execute JavaScript in the page context and return the result. "
            "Use sparingly — prefer semantic actions (click, type, snapshot) when possible. "
            "Useful for reading computed state or triggering custom JS logic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "JavaScript expression or statement to evaluate",
                },
            },
            "required": ["script"],
        },
    },
]


# ── Format adapters ───────────────────────────────────────────────────────────

def _to_claude_format(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic Claude tool format: {name, description, input_schema}."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["input_schema"],
    }


def _to_openai_format(tool: dict[str, Any]) -> dict[str, Any]:
    """OpenAI function-calling format: {type: function, function: {name, description, parameters}}."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


# Anthropic Claude tool_choice compatible format
TOOLS_FOR_CLAUDE: list[dict[str, Any]] = [_to_claude_format(t) for t in TOOLS]

# OpenAI function-calling compatible format
TOOLS_FOR_OPENAI: list[dict[str, Any]] = [_to_openai_format(t) for t in TOOLS]


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_tool(name: str) -> dict[str, Any] | None:
    """Return the tool definition for *name*, or None if not found."""
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


def get_tool_names() -> list[str]:
    """Return all registered tool names."""
    return [t["name"] for t in TOOLS]


def get_schema(name: str) -> dict[str, Any] | None:
    """Return the input_schema for *name*, or None if not found."""
    tool = get_tool(name)
    return tool["input_schema"] if tool else None
