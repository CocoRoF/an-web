# AN-Web

[English](README.md) | [한국어](README.ko.md)

[![PyPI](https://img.shields.io/pypi/v/an-web)](https://pypi.org/project/an-web/) [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**A Python-native headless browser engine for AI agents. It turns every web page into a structured, actionable semantic graph instead of pixels.**

## What problem it solves

Playwright and Puppeteer drive a full Chromium for human-written tests. An agent loop
(navigate, observe, decide, act) pays for that in install size, cold start, and output
that is a screenshot or a huge DOM/aria dump. AN-Web is built for that loop:

- `pip install an-web` is the whole setup. V8 comes from the `mini-racer` wheel; there is no browser download.
- Pages are returned as a `PageSemantics` model: page type, ranked actions, input fields, blocking elements (modals, cookie banners), and a role/name tree.
- Elements can be targeted semantically (`{"by": "role", "role": "button", "text": "Sign In"}`), not only by CSS selector.
- Policy (domain rules, rate limits, approvals), tracing and replay are built in.
- 13 tools, plus ready-made Anthropic/OpenAI tool schemas and an MCP server.

AN-Web is not a full browser: no screenshots, no iframes, no WebSocket, no hover/drag. See [Known Limitations](#known-limitations).

## Installation

Requires Python 3.12+.

```bash
pip install an-web              # engine + Python API
pip install "an-web[mcp]"       # also the `an-web-mcp` MCP server
```

Latest release on PyPI: 0.9.1. From source: `git clone https://github.com/CocoRoF/an-web && cd an-web && pip install -e ".[dev]"` (or `uv sync`).

Runtime dependencies: `httpx`, `brotli`, `selectolax`, `html5lib`, `pydantic`, `cssselect`, `mini-racer` (embedded V8; bundled in the wheel, no system packages).
Platform support follows the prebuilt `mini-racer` wheels.

## Quick Start

```python
import asyncio
from an_web import ANWebEngine

async def main():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://example.com")

        page = await session.snapshot()          # PageSemantics object
        print(page.title, page.page_type, len(page.primary_actions))

        await session.act({"tool": "extract", "query": "h1"})
        links = await session.act({"tool": "extract", "query": "a"})
        print(links["effects"]["count"])

asyncio.run(main())
```

Three core calls: `navigate(url)`, `snapshot()`, `act({...})`. Every tool goes through `act`.

```
ANWebEngine (async context manager)
  └── Session  (one per tab: own cookies, storage, JS runtime, history)
        navigate(url, timeout=None) · snapshot() · act(tool_call) · execute_script(js) · back() · close()
```

`engine.create_session(policy=None, session_id=None)`; sessions are also async context managers.

## Three levels of API

1. **`session.act(dict)`** returns a dict `{"status": "ok" | "failed" | "blocked", "action", "effects", "error", ...}`. It also accepts Anthropic `tool_use` blocks: `{"name": "click", "input": {...}, "type": "tool_use"}`.
2. **`ANWebToolInterface(session)`** (`from an_web.api import ...`) has typed helpers `navigate`, `click`, `type`, `snapshot`, `extract`, `eval_js`, `wait_for(condition, selector, timeout_ms)` and `run(tool_call)`. It records tool history; `history_as_trace()` exports it.
3. **`dispatch_tool(call, session, validate=True, collect_artifacts=True)`** is the low-level pipeline: parse, validate, normalize, policy check, dispatch, collect artifact.

## Tools

| Tool | Purpose | Key arguments |
|---|---|---|
| `navigate` | Load URL, run scripts, settle | `url` |
| `snapshot` | Semantic page state (dict) | |
| `click` | Click | `target` |
| `type` | Type into input | `target`, `text`, `append` |
| `clear` | Clear input | `target` |
| `select` | Choose option | `target`, `value`, `by_text` |
| `submit` | Submit form | `target` |
| `extract` | Pull data (css/structured/json/html) | `query` |
| `scroll` | Scroll | `delta_y`, or `target` to scroll into view |
| `wait_for` | Wait | `condition` (`network_idle` default, `dom_stable`, `selector`, `element_visible`), `selector`, `timeout_ms` (5000) |
| `eval_js` | Run JS; Promises are awaited | `script` |
| `fetch` | HTTP request with session cookies and policy, bypassing page JS | `url`, `method`, `headers`, `body` |
| `network` | Log of the page's runtime fetch/XHR; `index` returns one full body | `index` |

Notes:

- The `snapshot` tool returns camelCase keys: `pageType`, `title`, `url`, `primaryActions`, `inputs`, `blockingElements`, `semanticTree`, `snapshotId`. `session.snapshot()` returns the same data as a `PageSemantics` object with snake_case attributes.
- `fetch` effects: `status`, `ok`, `url`, `content_type`, `body` (capped at 200k chars), `json`, `truncated`. Relative URLs resolve against the current page.
- `network` effects: `count`, `requests` (each with `method`, `url`, `status`, `content_type`, `body_size`, and a body preview of 2048 chars by default).
- If client-side rendering leaves content out of the DOM, look in `network`/`fetch` first.
- `navigate` settle budget is 15 s by default; cap it with `session.navigate(url, timeout=3)`. If page JS wipes server-rendered content, the pre-JS DOM is restored and `dom_restored` is set in the effects.

### Targeting

`click`, `type`, `clear`, `select`, `submit` accept a CSS selector string or a dict:

```python
{"by": "role", "role": "button", "text": "Sign In"}   # ARIA role, optional text filter on the accessible name
{"by": "text", "text": "Forgot password?"}            # visible text (exact matches ranked first)
{"by": "semantic", "text": "submit button"}           # same text matching as "text"
{"by": "node_id", "node_id": "n42"}                   # node_id from a snapshot
```

### Extraction modes

```python
await session.act({"tool": "extract", "query": "ul.menu li a"})                    # css (default)
await session.act({"tool": "extract", "query": {                                    # structured
    "selector": ".product-card",
    "fields": {"name": ".product-name", "image": {"sel": "img", "attr": "src"}}}})
await session.act({"tool": "extract", "query": {"mode": "json", "selector": "script[type='application/ld+json']"}})
await session.act({"tool": "extract", "query": {"mode": "html", "selector": "article.main"}})
```

Results are in `result["effects"]["results"]` with `count` and `mode`.

## PageSemantics

`page.page_type`, `title`, `url`, `snapshot_id`, `primary_actions`, `inputs`, `blocking_elements`, `semantic_tree`, `to_dict()`.
`SemanticNode` has `node_id`, `tag`, `role`, `name`, `value`, `xpath`, `visible`, `is_interactive`, `affordances`, `attributes`, `children`, and `find_by_role()`, `find_interactive()`, `find_by_text(text, partial=True)`.

Page types the classifier can emit: `login_form`, `registration_form`, `checkout`, `search`, `search_results`, `product_detail`, `listing`, `article`, `dashboard`, `profile`, `settings`, `form`, `error`, `generic`, `empty`.

## MCP server

`an-web-mcp` exposes a tool surface modelled on playwright-mcp (needs the `mcp` extra):

```bash
claude mcp add an-web -- uvx --from 'an-web[mcp]' an-web-mcp
```

```json
{ "mcpServers": { "an-web": { "command": "uvx", "args": ["--from", "an-web[mcp]", "an-web-mcp"] } } }
```

Tools (14): `browser_navigate`, `browser_navigate_back`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_select_option`, `browser_wait_for`, `browser_evaluate`, `browser_extract`, `browser_fetch`, `browser_network_requests`, `browser_network_request`, `browser_console_messages`, `browser_close`.
Snapshots are compact trees with `[ref=nN]` handles; action tools take `element` (description) and `target` (a ref or CSS selector) and return a fresh snapshot.

| Variable | Effect |
|---|---|
| `ANWEB_ALLOWED_DOMAINS` | Comma-separated allowlist |
| `ANWEB_BLOCKED_DOMAINS` | Comma-separated blocklist |
| `ANWEB_NAV_TIMEOUT` | Navigation settle budget in seconds (default 15) |

## Using it with Claude / OpenAI

```python
from an_web.api import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI, get_tool_names, get_tool, get_schema
```

Minimal agent loop with the Anthropic SDK:

```python
import anthropic
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface, TOOLS_FOR_CLAUDE

async def run_agent(task: str, model: str):
    client = anthropic.Anthropic()
    async with ANWebEngine() as engine:
        tools = ANWebToolInterface(await engine.create_session())
        messages = [{"role": "user", "content": task}]
        while True:
            response = client.messages.create(model=model, max_tokens=4096,
                                              tools=TOOLS_FOR_CLAUDE, messages=messages)
            if response.stop_reason != "tool_use":
                return response.content[0].text
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    out = await tools.run({"name": block.name, "input": block.input})
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(out)})
            messages += [{"role": "assistant", "content": response.content},
                         {"role": "user", "content": results}]
```

## Policy and safety

Every action passes the `PolicyChecker`; blocked actions return `status: "blocked"`.

```python
from an_web.policy.rules import PolicyRules, NavigationScope
from an_web.policy.sandbox import SandboxLimits
from an_web.policy.approvals import ApprovalManager

PolicyRules.default()                                      # permissive, 120 req/min
PolicyRules.strict()                                       # 30 req/min, approval for navigate + submit
PolicyRules.sandboxed(allowed_domains=["example.com"])

policy = PolicyRules(
    allowed_domains=["example.com", "*.example.com"], denied_domains=["evil.com"],
    allowed_schemes=["https"], navigation_scope=NavigationScope.SAME_DOMAIN,  # also UNRESTRICTED, SAME_ORIGIN, PREFIX
    max_requests_per_minute=60, max_requests_per_hour=500,
    allow_form_submission=True, allow_file_download=False, require_approval_for=["submit"],
)
session = await engine.create_session(policy=policy)
```

- `SandboxLimits(max_requests, max_dom_nodes, max_script_ops, max_navigations, max_snapshots)` with presets `default()`, `strict()`, `unlimited()`.
- `ApprovalManager(auto_approve=False)`: `request(action, details)`, `grant(request_id)` / `approve`, `deny`, `grant_once(action)`, `grant_unlimited(glob_pattern)`, `is_approved`, `audit_log`. Each session owns one as `session.approvals`.

## Tracing and replay

```python
from an_web.tracing.logs import get_logger
from an_web.tracing.artifacts import ArtifactCollector
from an_web.tracing.replay import ReplayTrace, ReplayEngine

logger = get_logger("agent", session_id=session.session_id)   # StructuredLogger: info/error/..., action_context(), get_errors()
collector = ArtifactCollector(session_id=session.session_id)  # record_action_trace/js_exception/network/dom/semantic/policy_violation, get_by_kind(), summary()

trace = ReplayTrace.new(session_id="t1")
trace.add_step("navigate", {"url": "https://example.com"}, expected_status="ok")
result = await ReplayEngine().replay_trace(trace, session)    # result.succeeded, result.failed_steps
trace2 = ReplayTrace.from_json(trace.to_json())
```

Artifact kinds: `dom_snapshot`, `semantic_snapshot`, `network_trace`, `js_exception`, `action_trace`, `policy_violation`, `custom`. `dispatch_tool` records an action trace for each call.

## JavaScript and SPA support

Pages run in an embedded V8 (`mini-racer` 0.14, V8 14.x) with a Python-backed host API: DOM, events, timers, `fetch`/`XMLHttpRequest` (bridged to the async network layer), `localStorage`/`sessionStorage`, `location`/`history`, `MutationObserver`, `IntersectionObserver`/`ResizeObserver` (fire), `TextEncoder`/`TextDecoder`, `DOMParser`, `FormData`/`File`, `Blob`, `URL`, `postMessage`, and more. `session.execute_script(js)` and the `eval_js` tool run code in the page. Next.js/React and webpack pages have been verified to render end to end.

## Benchmarks

Measured 2026-07-03 against `playwright 1.61.0` on the same host with identical success criteria; harness and raw numbers are in [`benchmarks/`](benchmarks/). I did not re-run these for this README revision.

| Metric | AN-Web 0.9.1 | Playwright + Chromium |
|---|---|---|
| Disk after install | 111 MB | about 782 MB |
| Cold start (engine plus first page context) | 0.15 to 0.35 s | 3.3 s |
| Warm per-action latency | 0.04 ms | 1.9 ms |
| Peak RSS, 10-site crawl | 484 MB | 1,382 MB |
| Famous-sites score (10 sites) | 10/10 | 9/10 |

Playwright lost stackoverflow.com to an anti-bot 403; AN-Web is blocked by other sites that fingerprint plain HTTP clients (medium.com, npmjs.com, amazon.com), and Wikipedia costs AN-Web seconds of JS settle time (cap with `timeout=3`). See [`benchmarks/README.md`](benchmarks/README.md).

## Known Limitations

- **iframes**: appear as elements, but their documents are not loaded or scripted.
- **WebSocket**: not available (`fetch`/XHR are bridged).
- **Pointer realism**: no hover, drag-and-drop, or key chords; click/type/select/scroll/submit are semantic.
- **Screenshots**: none, by design.
- **Anti-bot walls**: AN-Web uses an ordinary HTTP-client TLS fingerprint; some sites block it (medium.com, npmjs.com 403, amazon.com 202).
- **Hydration**: client-fetched data reaches the snapshot once, but hydration mismatches can still duplicate static sections on some Next.js pages.
- **Partial renders**: a few JS-shell portals (daum.net, youtube.com) render only partially.

Rule of thumb: agent loops that read, extract, fill and submit suit AN-Web; flows that hover, drag, screenshot, pay inside an iframe or stream over WebSocket suit Playwright.

## Project layout

```
an_web/
  core/       ANWebEngine, Session, scheduler, snapshot manager
  dom/        nodes, document, selectors, mutation, semantics models
  browser/    HTML parser (selectolax + html5lib)
  js/         V8 bridge, JSRuntime, host Web API
  net/        httpx client, cookies, resource loader
  layout/     visibility, flow, hit-testing (layout-lite)
  semantic/   extractor, page-type classifier, roles, affordances
  actions/    the 13 tools
  api/        dispatch_tool, ANWebToolInterface, request models, tool schemas, rpc
  policy/     rules, checker, sandbox, approvals
  tracing/    artifacts, logs, replay
  mcp_server.py   an-web-mcp entry point
tests/        unit/ and integration/
benchmarks/   AN-Web vs Playwright harness
```

## Development

```bash
uv sync                       # or: pip install -e ".[dev]"
uv run pytest                 # 1582 passed, 1 skipped at the time of writing
uv run ruff check an_web/
uv run mypy an_web/
```

`smoke_real_world.py` and `test_*_*.py` at the repo root are live-site smoke scripts, not part of `tests/`.

## License

Licensed under the [Apache License 2.0](LICENSE).
