"""an-web-mcp — Model Context Protocol server for AN-Web.

Exposes the AN-Web browser-less engine to MCP clients (Claude Code,
Claude Desktop, any MCP host) with a tool surface modelled on
Microsoft's playwright-mcp, so agents already fluent in that contract
feel at home:

- ``browser_snapshot`` returns a compact accessibility-style tree where
  interactive elements carry ``[ref=nN]`` handles.
- Action tools take ``element`` (human-readable description, for
  auditability) + ``target`` (a ref from the latest snapshot, or a CSS
  selector).
- Every mutating tool returns the result AND a fresh snapshot, so the
  agent never needs a follow-up round trip.
- ``browser_fetch`` / ``browser_network_requests`` surface the data
  plane directly — the AI-native superpower of a browser-less engine.

Run::

    uvx --from 'an-web[mcp]' an-web-mcp
    # or: pip install 'an-web[mcp]' && an-web-mcp

Config via environment:

- ``ANWEB_ALLOWED_DOMAINS`` — comma-separated allowlist (default: all)
- ``ANWEB_BLOCKED_DOMAINS`` — comma-separated blocklist
- ``ANWEB_NAV_TIMEOUT`` — navigate settle budget in seconds (default 15)
"""
from __future__ import annotations

import os
import re
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:  # pragma: no cover
    raise SystemExit(
        "an-web-mcp requires the 'mcp' package.\n"
        "Install with: pip install 'an-web[mcp]'"
    ) from _exc

from an_web import ANWebEngine

# ── Session management ────────────────────────────────────────────────────────

_engine: ANWebEngine | None = None
_session: Any = None

# Maximum snapshot nodes rendered (token budget)
_SNAPSHOT_NODE_BUDGET = 400
_REF_PATTERN = re.compile(r"^(n\d+|js_\d+|py_\w+)$")


async def _get_session() -> Any:
    global _engine, _session
    if _engine is None:
        _engine = ANWebEngine()
        await _engine.__aenter__()
    if _session is None:
        policy = _policy_from_env()
        if policy is not None:
            _session = await _engine.create_session(policy=policy)
        else:
            _session = await _engine.create_session()
    return _session


def _policy_from_env() -> Any:
    allowed = [
        d.strip() for d in os.environ.get("ANWEB_ALLOWED_DOMAINS", "").split(",")
        if d.strip()
    ]
    blocked = [
        d.strip() for d in os.environ.get("ANWEB_BLOCKED_DOMAINS", "").split(",")
        if d.strip()
    ]
    if not allowed and not blocked:
        return None
    from an_web.policy.rules import PolicyRules
    if allowed:
        rules = PolicyRules.sandboxed(allowed_domains=allowed)
        rules.denied_domains = blocked
        return rules
    rules = PolicyRules.default()
    rules.denied_domains = blocked
    return rules


def _nav_timeout() -> float:
    try:
        return float(os.environ.get("ANWEB_NAV_TIMEOUT", "15"))
    except ValueError:
        return 15.0


def _resolve_target(target: str) -> str | dict[str, Any]:
    """A ref like ``n42`` becomes a node_id target; anything else is CSS."""
    target = target.strip()
    if _REF_PATTERN.match(target):
        return {"by": "node_id", "node_id": target}
    return target


# ── Snapshot rendering ────────────────────────────────────────────────────────

def _render_node(node: Any, lines: list[str], depth: int, budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    role = node.role or node.tag or ""
    name = (node.name or "").strip()
    if len(name) > 80:
        name = name[:77] + "..."
    interactive = getattr(node, "is_interactive", False)

    # Skip anonymous structural wrappers but keep walking their children
    skip_self = (
        role in ("generic", "none", "presentation", "") and not name
        and not interactive
    )
    if not skip_self:
        parts = [f"- {role}"]
        if name:
            parts.append(f' "{name}"')
        value = getattr(node, "value", None)
        if value:
            parts.append(f" [value={str(value)[:40]!r}]")
        if interactive:
            parts.append(f" [ref={node.node_id}]")
        lines.append("  " * depth + "".join(parts))
        budget[0] -= 1
        depth += 1

    for child in getattr(node, "children", []) or []:
        _render_node(child, lines, depth, budget)


async def _snapshot_text(session: Any, header: str = "") -> str:
    snap = await session.snapshot()
    lines: list[str] = []
    budget = [_SNAPSHOT_NODE_BUDGET]
    if snap.semantic_tree is not None:
        _render_node(snap.semantic_tree, lines, 0, budget)
    tree = "\n".join(lines) if lines else "(empty page)"
    if budget[0] <= 0:
        tree += "\n... (truncated at node budget; use browser_extract for details)"

    net_count = len(getattr(session, "_network_log", []))
    out = []
    if header:
        out.append(header)
    out.append("### Page")
    out.append(f"- URL: {session.current_url}")
    out.append(f"- Title: {snap.title}")
    out.append(f"- Page type: {snap.page_type}")
    if net_count:
        out.append(
            f"- Network: {net_count} runtime request(s) logged "
            "(inspect with browser_network_requests)"
        )
    out.append("\n### Snapshot")
    out.append("```yaml")
    out.append(tree)
    out.append("```")
    out.append(
        "\nInteract with elements via their [ref=...] handle, "
        "or a CSS selector."
    )
    return "\n".join(out)


def _describe_result(result: dict[str, Any]) -> str:
    status = result.get("status", "?")
    if status != "ok":
        return f"### Error\n{result.get('error', 'unknown error')}"
    effects = result.get("effects", {})
    keys = {
        k: v for k, v in effects.items()
        if k not in ("results", "requests") and not isinstance(v, (list, dict))
    }
    return "### Result\n" + ", ".join(f"{k}={v}" for k, v in keys.items())


# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "an-web",
    instructions=(
        "AN-Web: a browser-less web engine for AI agents. Navigate pages, "
        "read semantic snapshots, act on elements by [ref], and pull data "
        "directly from page APIs with browser_fetch when content is "
        "JS-rendered. No pixels, no Chromium — fast structured access."
    ),
)


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Navigate to a URL, execute its scripts, and return a semantic snapshot."""
    session = await _get_session()
    result = await session.navigate(url, timeout=_nav_timeout())
    if result.get("status") != "ok":
        return f"### Error\nNavigation failed: {result.get('error')}"
    return await _snapshot_text(session, _describe_result(result))


@mcp.tool()
async def browser_navigate_back() -> str:
    """Go back to the previous page in this session's history."""
    session = await _get_session()
    result = await session.back()
    if result.get("status") != "ok":
        return f"### Error\n{result.get('error', 'no history')}"
    return await _snapshot_text(session)


@mcp.tool()
async def browser_snapshot() -> str:
    """Capture the current page as a semantic accessibility-style tree.

    Interactive elements carry [ref=nN] handles for use as `target` in
    action tools. Better than a screenshot: structured and token-cheap.
    """
    session = await _get_session()
    return await _snapshot_text(session)


@mcp.tool()
async def browser_click(element: str, target: str) -> str:
    """Click an element.

    Args:
        element: Human-readable description of what you are clicking (for audit).
        target: Element [ref] from the latest snapshot, or a CSS selector.
    """
    session = await _get_session()
    result = await session.act({"tool": "click", "target": _resolve_target(target)})
    return await _snapshot_text(session, _describe_result(result))


@mcp.tool()
async def browser_type(
    element: str, target: str, text: str, submit: bool = False
) -> str:
    """Type text into an editable element.

    Args:
        element: Human-readable description of the field (for audit).
        target: Element [ref] from the latest snapshot, or a CSS selector.
        text: Text to type.
        submit: Submit the enclosing form afterwards.
    """
    session = await _get_session()
    result = await session.act({
        "tool": "type", "target": _resolve_target(target), "text": text,
    })
    if submit and result.get("status") == "ok":
        result = await session.act({
            "tool": "submit", "target": _resolve_target(target),
        })
    return await _snapshot_text(session, _describe_result(result))


@mcp.tool()
async def browser_select_option(element: str, target: str, value: str) -> str:
    """Select an option in a dropdown.

    Args:
        element: Human-readable description of the dropdown (for audit).
        target: Element [ref] from the latest snapshot, or a CSS selector.
        value: Option value (or visible text) to select.
    """
    session = await _get_session()
    result = await session.act({
        "tool": "select", "target": _resolve_target(target), "value": value,
    })
    return await _snapshot_text(session, _describe_result(result))


@mcp.tool()
async def browser_wait_for(
    time: float | None = None,
    text: str | None = None,
    text_gone: str | None = None,
) -> str:
    """Wait for time to pass, or for text to appear/disappear on the page."""
    import asyncio

    session = await _get_session()
    if time is not None:
        await asyncio.sleep(min(time, 30.0))
        return await _snapshot_text(session, f"### Result\nWaited {time}s")

    if text is None and text_gone is None:
        return "### Error\nProvide one of: time, text, text_gone"

    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        doc = getattr(session, "_current_document", None)
        body_text = doc.text_content if doc is not None else ""
        if text is not None and text in body_text:
            return await _snapshot_text(session, f"### Result\nText appeared: {text!r}")
        if text_gone is not None and text_gone not in body_text:
            return await _snapshot_text(session, f"### Result\nText gone: {text_gone!r}")
        await asyncio.sleep(0.2)
    return "### Error\nwait_for timed out after 10s"


@mcp.tool()
async def browser_evaluate(script: str) -> str:
    """Evaluate JavaScript in the page context; Promises are awaited.

    e.g. "document.title" or "fetch('/api/items').then(r => r.json())".
    """
    session = await _get_session()
    result = await session.act({"tool": "eval_js", "script": script})
    if result.get("status") != "ok":
        return f"### Error\n{result.get('error')}"
    effects = result.get("effects", {})
    return f"### Result\n{effects.get('result')}"


@mcp.tool()
async def browser_extract(
    selector: str, mode: str = "css", fields: dict[str, Any] | None = None
) -> str:
    """Extract data from the page by CSS selector.

    Args:
        selector: CSS selector for target elements.
        mode: 'css' (text+attrs), 'html' (markup), 'json' (embedded JSON),
              or 'structured' (per-item named fields).
        fields: For mode='structured': {"field": ".sub-selector", ...}.
    """
    import json as _json

    session = await _get_session()
    if mode == "structured":
        query: Any = {"mode": "structured", "selector": selector,
                      "fields": fields or {}}
    elif mode in ("json", "html"):
        query = {"mode": mode, "selector": selector}
    else:
        query = selector
    result = await session.act({"tool": "extract", "query": query})
    if result.get("status") != "ok":
        return f"### Error\n{result.get('error')}"
    effects = result.get("effects", {})
    payload = _json.dumps(effects.get("results", []), ensure_ascii=False)
    if len(payload) > 100_000:
        payload = payload[:100_000] + "... (truncated)"
    return f"### Result\ncount={effects.get('count', 0)}\n```json\n{payload}\n```"


@mcp.tool()
async def browser_fetch(
    url: str, method: str = "GET", body: str | None = None
) -> str:
    """Perform an HTTP request with the session's cookies and policy.

    The reliable way to pull data from the APIs a page uses — e.g. when
    content is rendered client-side and missing from the snapshot, or
    after browser_network_requests reveals an endpoint. Relative URLs
    resolve against the current page.
    """
    import json as _json

    session = await _get_session()
    result = await session.act({
        "tool": "fetch", "url": url, "method": method, "body": body,
    })
    if result.get("status") != "ok":
        return f"### Error\n{result.get('error')}"
    effects = result.get("effects", {})
    out = [
        "### Result",
        f"- HTTP {effects.get('status')} {effects.get('url')}",
        f"- content-type: {effects.get('content_type')}",
    ]
    if effects.get("json") is not None:
        payload = _json.dumps(effects["json"], ensure_ascii=False)
        if len(payload) > 100_000:
            payload = payload[:100_000] + "... (truncated)"
        out.append(f"```json\n{payload}\n```")
    else:
        body_text = effects.get("body") or ""
        out.append(f"```\n{body_text[:100_000]}\n```")
    return "\n".join(out)


@mcp.tool()
async def browser_network_requests() -> str:
    """List fetch/XHR requests the page made at runtime, with body previews.

    Pages often load their real content this way — check here when data
    is missing from the DOM, then use browser_fetch or
    browser_network_request to read full payloads.
    """
    session = await _get_session()
    result = await session.act({"tool": "network"})
    effects = result.get("effects", {})
    reqs = effects.get("requests", [])
    if not reqs:
        return "### Result\nNo runtime network activity recorded."
    lines = ["### Result"]
    for r in reqs:
        lines.append(
            f"- [{r['index']}] {r['method']} {r['url']} → {r['status']} "
            f"({r['content_type'] or 'no content-type'}, {r['body_size']}B)"
        )
    return "\n".join(lines)


@mcp.tool()
async def browser_network_request(index: int) -> str:
    """Return the full response body of a logged network request by index."""
    session = await _get_session()
    result = await session.act({"tool": "network", "index": index})
    if result.get("status") != "ok":
        return f"### Error\n{result.get('error')}"
    reqs = result.get("effects", {}).get("requests", [])
    if not reqs:
        return "### Error\nrequest not found"
    r = reqs[0]
    return (
        f"### Result\n{r['method']} {r['url']} → {r['status']}\n"
        f"```\n{r['body']}\n```"
    )


@mcp.tool()
async def browser_close() -> str:
    """Close the current session (a fresh one is created on next use)."""
    global _session
    if _session is not None:
        try:
            await _session.close()
        except Exception:
            pass
        _session = None
    return "### Result\nSession closed."


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
