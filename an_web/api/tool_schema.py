"""
AI Tool schema definitions — JSON Schema compatible.

These schemas are designed to be passed directly to AI models
(Claude, GPT-4, etc.) as tool definitions.
"""
from __future__ import annotations

from typing import Any


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
                    },
                    "role": {"type": "string", "description": "ARIA role (e.g. 'button')"},
                    "text": {"type": "string", "description": "Accessible name or visible text"},
                    "node_id": {"type": "string", "description": "Internal node ID"},
                    "name": {"type": "string", "description": "Accessible name filter"},
                },
                "required": ["by"],
            },
        ]
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "navigate",
        "description": (
            "Navigate to a URL and wait for the page to load. "
            "Returns action result with final URL and load status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "snapshot",
        "description": (
            "Get the current page's semantic state as a structured world model. "
            "Returns page type, primary actions, input fields, and full semantic tree. "
            "Use this to understand what's on the page before acting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "click",
        "description": (
            "Click an element. Prefer semantic targeting over CSS selectors. "
            "After click, observe effects (navigation, modal, DOM changes)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },
    {
        "name": "type",
        "description": (
            "Type text into an input field, textarea, or contenteditable element. "
            "Dispatches input and change events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["target", "text"],
        },
    },
    {
        "name": "clear",
        "description": "Clear the value of an input field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },
    {
        "name": "select",
        "description": "Select an option from a <select> dropdown by value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
                "value": {"type": "string", "description": "Option value to select"},
            },
            "required": ["target", "value"],
        },
    },
    {
        "name": "submit",
        "description": "Submit a form. Triggers form submission and waits for navigation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": _target_schema(),
            },
            "required": ["target"],
        },
    },
    {
        "name": "extract",
        "description": (
            "Extract structured data from the page using a CSS selector or semantic query. "
            "Returns a list of matching elements with their text and attributes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "CSS selector (e.g. 'table tr') or semantic query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page or a specific element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {**_target_schema(), "description": "Element to scroll (omit for page)"},
                "delta_x": {"type": "integer", "default": 0},
                "delta_y": {"type": "integer", "default": 300, "description": "Pixels to scroll"},
            },
        },
    },
    {
        "name": "wait_for",
        "description": "Wait until a condition is satisfied before proceeding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "enum": ["network_idle", "dom_stable", "element_visible"],
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector (required for element_visible)",
                },
                "timeout_ms": {
                    "type": "integer",
                    "default": 5000,
                    "description": "Max wait in milliseconds",
                },
            },
            "required": ["condition"],
        },
    },
    {
        "name": "eval_js",
        "description": (
            "Execute JavaScript in the page context and return the result. "
            "Use sparingly — prefer semantic actions when possible."
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

# Anthropic Claude tool_choice compatible format
TOOLS_FOR_CLAUDE = [
    {
        "name": t["name"],
        "description": t["description"],
        "input_schema": t["input_schema"],
    }
    for t in TOOLS
]
