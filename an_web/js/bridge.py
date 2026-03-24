"""
JS <-> Python object marshalling for AN-Web QuickJS bridge.

Design principles:
- Use JSON as the lingua franca between JS and Python where possible
- Python callables registered via ctx.add_callable() receive/return JSON strings
- Complex objects (DOM nodes, etc.) serialised to stable JSON representations
- JSError captures structured JS exception info
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

# ── JS Exception wrapper ────────────────────────────────────────────────────


@dataclass
class JSError(Exception):
    """Wraps a JavaScript exception thrown during eval/call."""

    message: str
    stack: str = ""
    js_type: str = "Error"      # Error, TypeError, ReferenceError, etc.
    raw: Any = field(default=None, repr=False)

    def __str__(self) -> str:
        if self.stack:
            return f"{self.js_type}: {self.message}\n{self.stack}"
        return f"{self.js_type}: {self.message}"

    @classmethod
    def from_quickjs_exception(cls, exc: Exception) -> "JSError":
        """Convert a quickjs.JSException into a structured JSError."""
        raw_msg = str(exc)
        lines = raw_msg.splitlines()
        first = lines[0] if lines else raw_msg
        stack = "\n".join(lines[1:]) if len(lines) > 1 else ""

        if ":" in first:
            js_type, _, message = first.partition(":")
            js_type = js_type.strip()
            message = message.strip()
        else:
            js_type = "Error"
            message = first.strip()

        return cls(message=message, stack=stack, js_type=js_type, raw=exc)


# ── Result container ─────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    """Structured result from JSRuntime.eval()."""

    value: Any = None
    error: JSError | None = None
    ok: bool = True

    @classmethod
    def success(cls, value: Any) -> "EvalResult":
        return cls(value=value, ok=True)

    @classmethod
    def failure(cls, error: JSError) -> "EvalResult":
        return cls(error=error, ok=False)

    def unwrap(self) -> Any:
        """Return value or raise JSError."""
        if not self.ok:
            raise self.error  # type: ignore[misc]
        return self.value


# ── Type conversion ───────────────────────────────────────────────────────────


def py_to_js(value: Any) -> Any:
    """
    Convert a Python value to a JS-compatible form.

    quickjs-py accepts: str, int, float, bool, None.
    Everything else must be serialised; the caller is responsible for
    JSON.parse() on the JS side when needed.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): py_to_js(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [py_to_js(v) for v in value]
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def js_to_py(value: Any) -> Any:
    """
    Convert a value returned by quickjs-py to a native Python value.

    quickjs-py returns:
    - int / float / bool / str / None  for JS primitives
    - _quickjs.Object                  for JS objects/arrays (has .json())
    - quickjs.JSException              raised for errors (handled by caller)
    """
    try:
        if hasattr(value, "json"):
            raw = value.json()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except Exception:
        pass
    return value


def js_to_py_string(value: Any) -> str:
    """Ensure a JS result becomes a Python str."""
    converted = js_to_py(value)
    if converted is None:
        return ""
    return str(converted)


# ── JSON-bridge callable factory ──────────────────────────────────────────────


def make_json_callable(fn: Callable[..., Any]) -> Callable[..., str]:
    """
    Wrap *fn* so that it integrates cleanly with quickjs's add_callable():

    - Arguments arriving from JS as JSON strings are auto-decoded.
    - The return value is JSON-encoded back to a string.
    - Exceptions are returned as ``{"__error__": "..."}`` JSON.

    Usage in host_api.py::

        ctx.add_callable("_pyQS", make_json_callable(query_selector_fn))

    In JS::

        var el = JSON.parse(_pyQS(JSON.stringify({selector: ".btn"})));
    """
    def wrapper(*args: Any) -> str:
        decoded: list[Any] = []
        for a in args:
            if isinstance(a, str):
                try:
                    decoded.append(json.loads(a))
                except json.JSONDecodeError:
                    decoded.append(a)
            else:
                decoded.append(a)
        try:
            result = fn(*decoded)
            return json.dumps(result, default=str)
        except Exception as exc:
            log.debug("JSON callable '%s' error: %s", fn.__name__, exc)
            return json.dumps({"__error__": str(exc)})

    wrapper.__name__ = getattr(fn, "__name__", "js_callable")
    return wrapper


# ── DOM node marshalling ──────────────────────────────────────────────────────


def marshal_element(element: Any) -> dict[str, Any]:
    """
    Convert a DOM Element to a JSON-serialisable dict for JS exposure.

    The JS shim layer (host_api.py) wraps this dict in a JS object
    with helper methods like getAttribute(), querySelector(), etc.
    """
    if element is None:
        return {}

    from an_web.dom.nodes import Element, TextNode

    base: dict[str, Any] = {
        "nodeId": getattr(element, "node_id", ""),
        "nodeType": 1,   # ELEMENT_NODE
        "tag": getattr(element, "tag", ""),
        "tagName": getattr(element, "tag", "").upper(),
        "id": (getattr(element, "attributes", {}) or {}).get("id", ""),
        "className": (getattr(element, "attributes", {}) or {}).get("class", ""),
        "attributes": dict(getattr(element, "attributes", {}) or {}),
        "textContent": getattr(element, "text_content", ""),
        "innerHTML": _inner_html(element),
        "isInteractive": getattr(element, "is_interactive", False),
        "visibilityState": getattr(element, "visibility_state", "visible"),
        "semanticRole": getattr(element, "semantic_role", None),
        "stableSelector": getattr(element, "stable_selector", None),
        "children": [],
    }

    # Shallow children (tag + key fields only — avoid deep serialisation)
    for child in getattr(element, "children", []):
        if isinstance(child, Element):
            base["children"].append({
                "nodeId": child.node_id,
                "nodeType": 1,
                "tag": child.tag,
                "tagName": child.tag.upper(),
                "id": child.attributes.get("id", ""),
                "className": child.attributes.get("class", ""),
                "attributes": dict(child.attributes),
                "textContent": child.text_content,
            })
        elif isinstance(child, TextNode):
            base["children"].append({
                "nodeId": child.node_id,
                "nodeType": 3,   # TEXT_NODE
                "tag": "#text",
                "tagName": "#text",
                "id": "",
                "className": "",
                "attributes": {},
                "textContent": child.data,
            })

    return base


def _inner_html(element: Any) -> str:
    """Very lightweight innerHTML approximation."""
    parts: list[str] = []
    for child in getattr(element, "children", []):
        from an_web.dom.nodes import TextNode, Element
        if isinstance(child, TextNode):
            parts.append(child.data)
        elif isinstance(child, Element):
            attrs = "".join(
                f' {k}="{v}"' for k, v in child.attributes.items()
            )
            inner = _inner_html(child)
            parts.append(f"<{child.tag}{attrs}>{inner}</{child.tag}>")
    return "".join(parts)


def marshal_document(doc: Any) -> dict[str, Any]:
    """Serialise a Document's top-level metadata."""
    return {
        "title": getattr(doc, "title", "") or "",
        "url": getattr(doc, "url", "about:blank") or "about:blank",
        "readyState": "complete",
        "nodeType": 9,    # DOCUMENT_NODE
    }
