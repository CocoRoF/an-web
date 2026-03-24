"""JS ↔ Python object marshalling."""
from __future__ import annotations
from typing import Any


def py_to_js(value: Any) -> Any:
    """Convert Python value to JS-compatible type."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): py_to_js(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [py_to_js(v) for v in value]
    return str(value)


def js_to_py(value: Any) -> Any:
    """Convert JS result back to Python type."""
    return value  # quickjs-py handles basic type coercion
