# AN-Web — AI-Native Web Browser Engine

[English](README.md) | [한국어](README.ko.md)

**AN-Web** is a Python-native headless browser engine purpose-built for AI agents.
Instead of rendering pixels for human eyes, it executes the web as an **actionable state machine** — every page becomes a structured semantic graph that an agent can reason over and act upon.

```
Navigate → Snapshot → Decide → Act → Observe → Repeat
```

The core interface is intentionally minimal: **3 methods** are all you need.

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    await session.navigate("https://example.com")     # 1. Load
    page   = await session.snapshot()                  # 2. Observe
    result = await session.act({"tool": "click", "target": "#btn"})  # 3. Act
```

---

## Table of Contents

- [Why AN-Web?](#why-an-web)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [Usage Patterns — Three Levels of API](#usage-patterns--three-levels-of-api)
- [All 11 Tools Reference](#all-11-tools-reference)
- [Semantic Targeting](#semantic-targeting)
- [Data Extraction](#data-extraction)
- [PageSemantics — The AI World Model](#pagesemantics--the-ai-world-model)
- [AI Model Integration (Claude / OpenAI)](#ai-model-integration-claude--openai)
- [Policy & Safety](#policy--safety)
- [Tracing & Replay](#tracing--replay)
- [JavaScript Execution & SPA Support](#javascript-execution--spa-support)
- [Architecture](#architecture)
- [Testing](#testing)
- [API Reference Summary](#api-reference-summary)
- [License](#license)
- [Contributing](#contributing)

---

## Why AN-Web?

Standard headless browsers (Playwright, Puppeteer) were designed for human-driven testing.
AN-Web is designed **from scratch** for the AI agent loop:

| Concern | Traditional Headless | AN-Web |
|---|---|---|
| **Primary output** | Screenshots / DOM strings | `PageSemantics` — structured world model |
| **JS engine** | V8 (full Chromium) | QuickJS (lightweight, embeddable) |
| **Latency** | 500 ms+ cold start | < 50 ms per action |
| **Memory** | 300–800 MB | ~30 MB |
| **Action targeting** | CSS selectors / XPath only | Semantic: `{"by": "role", "role": "button", "text": "Sign In"}` |
| **Policy & safety** | None built-in | Domain rules, rate limits, sandbox, approval flows |
| **Observability** | External tracing | First-class `ArtifactCollector`, `StructuredLogger`, `ReplayEngine` |
| **SPA support** | Full V8 | QuickJS + host Web API (webpack 5, React 18, jQuery) |

---

## Installation

**Requires:** Python 3.12+

```bash
pip install an-web
```

Or install from source:

```bash
git clone https://github.com/CocoRoF/an-web
cd an-web
pip install -e .

# With dev tools (pytest, ruff, mypy)
pip install -e ".[dev]"
```

**Dependencies** (all installed automatically):

| Package | Purpose |
|---|---|
| `httpx` | Async HTTP client with redirect & cookie support |
| `selectolax` | Fast HTML parser (Lexbor backend) |
| `html5lib` | Spec-compliant fallback parser |
| `pydantic` | Request/response validation |
| `quickjs` | Embedded JavaScript engine |
| `cssselect` | CSS selector parsing |

---

## Quick Start

### 3 Lines of Core Logic

```python
import asyncio
from an_web import ANWebEngine

async def main():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://example.com")
        page = await session.snapshot()

        print(page.title)               # "Example Domain"
        print(page.page_type)           # "generic"
        print(len(page.primary_actions))  # interactive elements count

asyncio.run(main())
```

Three method calls: `navigate()` → `snapshot()` → done.

### Navigate → Type → Click → Verify

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    await session.navigate("https://example.com/login")
    await session.act({"tool": "type", "target": "#email", "text": "user@example.com"})
    await session.act({"tool": "type", "target": "#password", "text": "secret123"})
    await session.act({"tool": "click", "target": "#login-btn"})

    page = await session.snapshot()
    print(page.url)  # redirected after login
```

Every interaction uses the same `session.act({...})` pattern.
One method, 11 tools, zero boilerplate.

---

## Core Concepts

### The Three Pillars

| Concept | What It Does | Method |
|---|---|---|
| **Navigate** | Load a URL, execute JS, settle the page | `session.navigate(url)` |
| **Snapshot** | Get the page as a structured semantic model | `session.snapshot()` |
| **Act** | Perform an action (click, type, extract, ...) | `session.act({...})` |

### ANWebEngine → Session → Action

```
ANWebEngine (process-level, async context manager)
  └── Session (one per "browser tab")
        ├── navigate(url)       → load page, run JS, settle
        ├── snapshot()          → return PageSemantics object
        ├── act({tool, ...})    → execute any of the 11 tools
        ├── execute_script(js)  → direct JavaScript evaluation
        ├── back()              → navigate to previous URL
        └── close()             → cleanup resources
```

```python
from an_web import ANWebEngine

async with ANWebEngine() as engine:
    # Create sessions (independent browser tabs)
    session1 = await engine.create_session()
    session2 = await engine.create_session()
    # Each session has its own cookies, storage, JS runtime, history

    # Sessions are also async context managers
    async with await engine.create_session() as session3:
        await session3.navigate("https://example.com")
    # session3 is automatically closed here
```

---

## Usage Patterns — Three Levels of API

AN-Web provides three levels of API so you can choose the right abstraction for your use case:

### Level 1: `session.act()` — The Universal Interface

**Simplest. Recommended for most use cases.**

One method handles all 11 tools. The input is a plain dict:

```python
async with ANWebEngine() as engine:
    session = await engine.create_session()

    # Navigate
    await session.act({"tool": "navigate", "url": "https://example.com"})

    # Get page state
    result = await session.act({"tool": "snapshot"})

    # Click
    await session.act({"tool": "click", "target": "#submit"})

    # Type
    await session.act({"tool": "type", "target": "#search", "text": "hello"})

    # Extract data
    result = await session.act({"tool": "extract", "query": "h1"})

    # Execute JavaScript
    result = await session.act({"tool": "eval_js", "script": "document.title"})
```

Every call returns a dict with:
```python
{
    "status": "ok",        # "ok" | "failed" | "blocked"
    "action": "click",     # tool name
    "effects": {...},      # tool-specific results
    "error": None,         # error message if failed
}
```

`session.act()` also accepts Anthropic's tool_use format:
```python
await session.act({
    "name": "click",
    "input": {"target": "#btn"},
    "type": "tool_use"
})
```

### Level 2: `ANWebToolInterface` — Typed Helper Methods

Named methods with IDE autocompletion. Automatically records tool history for replay.

```python
from an_web.api import ANWebToolInterface

async with ANWebEngine() as engine:
    session = await engine.create_session()
    tools = ANWebToolInterface(session)

    await tools.navigate("https://example.com/login")
    await tools.type("#email", "user@example.com")
    await tools.type("#password", "secret123")
    await tools.click("#login-btn")

    snap = await tools.snapshot()    # returns dict
    data = await tools.extract("table.results tr")

    # Also supports the universal run() method
    await tools.run({"tool": "scroll", "delta_y": 500})

    # Export session as a ReplayTrace
    trace = tools.history_as_trace()
```

Available methods:
| Method | Signature |
|---|---|
| `navigate(url)` | Load a URL |
| `click(target)` | Click an element |
| `type(target, text)` | Type text into an input |
| `snapshot()` | Get page state as dict |
| `extract(query)` | Extract data from page |
| `eval_js(script)` | Execute JavaScript |
| `wait_for(condition, selector?, timeout_ms?)` | Wait for a condition |
| `run(tool_call)` | Execute any tool call dict |

### Level 3: `dispatch_tool()` — Low-Level with Full Control

Direct function call with validation and artifact collection toggles:

```python
from an_web.api import dispatch_tool

result = await dispatch_tool(
    {"tool": "navigate", "url": "https://example.com"},
    session,
    validate=True,           # Pydantic request validation (default: True)
    collect_artifacts=True,  # record action trace artifact (default: True)
)
```

Pipeline: Parse → Validate → Normalize → Policy Check → Dispatch → Collect Artifact → Return.

---

## All 11 Tools Reference

### `navigate` — Load a URL

```python
await session.act({"tool": "navigate", "url": "https://example.com"})
```
Fetches the URL, parses HTML, builds the DOM, executes scripts (inline → deferred), dispatches `DOMContentLoaded` and `load` events, and settles the page.

### `snapshot` — Get Semantic Page State

```python
result = await session.act({"tool": "snapshot"})

result["page_type"]       # "login_form", "search", "article", "listing", ...
result["title"]           # page title
result["url"]             # current URL
result["primary_actions"] # ranked interactive elements
result["inputs"]          # form fields
result["blocking_elements"]  # modals, cookie banners
result["semantic_tree"]   # full page tree
```

> **Note:** `session.snapshot()` returns a `PageSemantics` object with attribute access.
> `session.act({"tool": "snapshot"})` returns the same data as a plain dict.

### `click` — Click an Element

```python
await session.act({"tool": "click", "target": "#submit-btn"})
await session.act({"tool": "click", "target": {"by": "role", "role": "button", "text": "Sign In"}})
```

### `type` — Type Text into an Input

```python
await session.act({"tool": "type", "target": "#search", "text": "hello world"})
await session.act({"tool": "type", "target": "#search", "text": " more", "append": True})
```

### `clear` — Clear an Input Field

```python
await session.act({"tool": "clear", "target": "#search"})
```

### `select` — Select a Dropdown Option

```python
await session.act({"tool": "select", "target": "#country", "value": "KR"})
await session.act({"tool": "select", "target": "#country", "value": "South Korea", "by_text": True})
```

### `submit` — Submit a Form

```python
await session.act({"tool": "submit", "target": "form#login"})
await session.act({"tool": "submit", "target": {"by": "role", "role": "form"}})
```

### `extract` — Extract Data from the Page

```python
result = await session.act({"tool": "extract", "query": "h1"})
# result["effects"]["results"] → [{"tag": "h1", "text": "Hello World", ...}]
```
See [Data Extraction](#data-extraction) for all 4 modes.

### `scroll` — Scroll the Page

```python
await session.act({"tool": "scroll", "delta_y": 500})       # scroll down 500px
await session.act({"tool": "scroll", "delta_y": -300})      # scroll up 300px
await session.act({"tool": "scroll", "target": "#section"}) # scroll element into view
```

### `wait_for` — Wait for a Condition

```python
await session.act({"tool": "wait_for", "condition": "network_idle"})
await session.act({"tool": "wait_for", "condition": "dom_stable", "timeout_ms": 3000})
await session.act({"tool": "wait_for", "condition": "selector", "selector": "#results"})
```

### `eval_js` — Execute JavaScript

```python
result = await session.act({"tool": "eval_js", "script": "document.title"})
result = await session.act({
    "tool": "eval_js",
    "script": "Array.from(document.querySelectorAll('a')).map(a => a.href)"
})
```

---

## Semantic Targeting

Action tools (`click`, `type`, `clear`, `select`, `submit`) support five target resolution strategies:

### 1. CSS Selector (String)
```python
await session.act({"tool": "click", "target": "#login-btn"})
await session.act({"tool": "click", "target": "button[type=submit]"})
await session.act({"tool": "type",  "target": "input[name=email]", "text": "user@example.com"})
```

### 2. ARIA Role + Text (Recommended for AI Agents)
```python
await session.act({"tool": "click", "target": {"by": "role", "role": "button", "text": "Sign In"}})
await session.act({"tool": "type",  "target": {"by": "role", "role": "textbox", "name": "Email"}, "text": "user@example.com"})
await session.act({"tool": "click", "target": {"by": "role", "role": "link", "text": "Forgot password?"}})
```

### 3. Visible Text Match
```python
await session.act({"tool": "click", "target": {"by": "text", "text": "Forgot password?"}})
```

### 4. Node ID (From Snapshot)
```python
page = await session.snapshot()
# Use the node_id from the semantic tree
await session.act({"tool": "click", "target": {"by": "node_id", "node_id": "n42"}})
```

### 5. General Semantic Query
```python
await session.act({"tool": "click", "target": {"by": "semantic", "text": "submit button"}})
```

---

## Data Extraction

The `extract` tool supports four modes for different extraction needs:

### CSS Mode (Default)

Extract elements matching a CSS selector:
```python
result = await session.act({"tool": "extract", "query": "h1"})
# → {"effects": {"count": 1, "results": [{"tag": "h1", "text": "Hello World", "node_id": "n5"}]}}

result = await session.act({"tool": "extract", "query": "ul.menu li a"})
# → {"effects": {"count": 5, "results": [{"tag": "a", "text": "Home", ...}, ...]}}
```

### Structured Mode

Extract named fields per matching item — ideal for tables, product lists, search results:
```python
result = await session.act({
    "tool": "extract",
    "query": {
        "selector": ".product-card",
        "fields": {
            "name":  ".product-name",
            "price": ".product-price",
            "image": {"sel": "img", "attr": "src"},
            "url":   {"sel": "a", "attr": "href"},
        }
    }
})
# → {"effects": {"count": 10, "results": [
#     {"name": "Widget A", "price": "$9.99", "image": "/img/a.jpg", "url": "/product/a"},
#     ...
# ]}}
```

### JSON Mode

Parse embedded JSON (e.g., `<script type="application/ld+json">`):
```python
result = await session.act({
    "tool": "extract",
    "query": {"mode": "json", "selector": "script[type='application/ld+json']"}
})
```

### HTML Mode

Get raw outer HTML of matched elements:
```python
result = await session.act({
    "tool": "extract",
    "query": {"mode": "html", "selector": "article.main"}
})
```

---

## PageSemantics — The AI World Model

When you call `session.snapshot()`, you get a `PageSemantics` object — the structured representation of the entire page that an AI agent can reason over:

```python
page = await session.snapshot()

# Page-level metadata
page.page_type            # "login_form" | "search" | "listing" | "article" | "dashboard" | ...
page.title                # page title
page.url                  # current URL
page.snapshot_id          # unique ID for this snapshot

# Pre-classified element categories (for quick agent decisions)
page.primary_actions      # ranked interactive elements: buttons, links, submits
page.inputs               # form fields: textbox, select, checkbox, radio
page.blocking_elements    # modals, cookie banners, overlays

# Full page structure
page.semantic_tree        # root SemanticNode — full hierarchical tree

# Serialize for AI model context
page_dict = page.to_dict()
```

### SemanticNode — Elements in the Tree

Each element in `semantic_tree` is a `SemanticNode`:

```python
node = page.semantic_tree

node.node_id          # stable ID for targeting: "n42"
node.tag              # HTML tag: "button", "input", "a", "div", ...
node.role             # ARIA role: "button", "textbox", "link", "navigation", ...
node.name             # accessible name (text content, aria-label, etc.)
node.value            # current value for inputs
node.xpath            # XPath to this element
node.is_interactive   # can the AI interact with this? (click, type, etc.)
node.visible          # is it visible on page?
node.affordances      # what actions are possible: ["clickable", "typeable", "submittable"]
node.attributes       # HTML attributes dict
node.children         # child SemanticNode list

# Search methods
buttons     = node.find_by_role("button")
interactive = node.find_interactive()
matches     = node.find_by_text("Sign In", partial=True)
```

### Page Type Classification

AN-Web automatically classifies pages into semantic types:

| `page_type` | Description | Example |
|---|---|---|
| `login_form` | Login / authentication page | GitHub login |
| `search` | Search input page | Google home |
| `search_results` | Search results listing | Google results |
| `listing` | Item list (products, articles) | Amazon category |
| `article` | Long-form content | Blog post |
| `dashboard` | Dashboard / admin panel | Analytics page |
| `form` | Generic form | Contact form |
| `error` | Error page (404, 500) | Not Found |
| `generic` | Other | Landing page |

---

## AI Model Integration (Claude / OpenAI)

### Ready-Made Tool Schemas

AN-Web ships tool schemas in both Anthropic and OpenAI formats. Pass them directly to your AI model:

```python
from an_web.api import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI

# Anthropic Claude
response = client.messages.create(
    model="claude-opus-4-6",
    tools=TOOLS_FOR_CLAUDE,             # ← plug in directly
    messages=[{"role": "user", "content": "Search for 'Python asyncio' on Google"}],
)

# OpenAI / compatible APIs
response = client.chat.completions.create(
    model="gpt-4o",
    tools=TOOLS_FOR_OPENAI,             # ← plug in directly
    messages=[...],
)
```

### Complete Agent Loop Example

```python
import anthropic
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface, TOOLS_FOR_CLAUDE

async def run_agent(task: str):
    client = anthropic.Anthropic()

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        tools = ANWebToolInterface(session)

        messages = [{"role": "user", "content": task}]

        while True:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                tools=TOOLS_FOR_CLAUDE,
                messages=messages,
            )

            # Check if the model wants to use a tool
            if response.stop_reason != "tool_use":
                # Model is done — print final answer
                print(response.content[0].text)
                break

            # Execute each tool call
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await tools.run({
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            # Feed results back to the model
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
```

### Tool Schema Utilities

```python
from an_web.api import get_tool_names, get_tool, get_schema

get_tool_names()        # ["navigate", "snapshot", "click", "type", ...]
get_tool("navigate")    # full schema dict for one tool
get_schema("claude")    # all schemas in Anthropic format
get_schema("openai")    # all schemas in OpenAI format
```

---

## Policy & Safety

AN-Web has built-in safety controls. Every action is checked by the `PolicyChecker` before execution.

### Quick Presets

```python
from an_web.policy.rules import PolicyRules

# Permissive (default) — all domains, 120 req/min
policy = PolicyRules.default()

# Strict — 30 req/min, approval required for navigate + submit
policy = PolicyRules.strict()

# Sandboxed — locked to specific domains only
policy = PolicyRules.sandboxed(allowed_domains=["example.com", "api.example.com"])
```

### Custom Policy

```python
from an_web.policy.rules import PolicyRules, NavigationScope

policy = PolicyRules(
    allowed_domains=["example.com", "*.example.com"],
    denied_domains=["evil.com"],
    allowed_schemes=["https"],                        # block http
    navigation_scope=NavigationScope.SAME_DOMAIN,
    max_requests_per_minute=60,
    max_requests_per_hour=500,
    allow_form_submission=True,
    allow_file_download=False,
    require_approval_for=["submit"],                  # human-in-the-loop for forms
)

async with ANWebEngine() as engine:
    session = await engine.create_session(policy=policy)

    # Allowed
    await session.navigate("https://example.com")             # ✓

    # Blocked — returns {"status": "blocked", ...}
    result = await session.act({"tool": "navigate", "url": "https://evil.com"})
    print(result["status"])  # "blocked"
```

### Sandbox Resource Limits

```python
from an_web.policy.sandbox import Sandbox, SandboxLimits

limits = SandboxLimits(
    max_requests=100,
    max_dom_nodes=10_000,
    max_navigations=20,
)

# Presets
SandboxLimits.default()     # balanced limits
SandboxLimits.strict()      # tight limits
SandboxLimits.unlimited()   # no limits
```

### Approval Flows (Human-in-the-Loop)

```python
from an_web.policy.approvals import ApprovalManager

approvals = ApprovalManager(auto_approve=False)

# Selectively approve actions
approvals.grant_once("submit")                              # one-time
approvals.grant_pattern("navigate:https://example.com/*")   # pattern-based
```

---

## Tracing & Replay

### Structured Logging

```python
from an_web.tracing.logs import get_logger

logger = get_logger("my_agent", session_id=session.session_id)
logger.info("Starting login flow")

# Tag subsequent logs with an action context
logger.action_context("login_step_1")

# Retrieve logs
errors   = logger.get_errors()
all_logs = logger.get_all()
```

### Artifact Collection

Every tool call automatically records an artifact. You can also record custom ones:

```python
from an_web.tracing.artifacts import ArtifactCollector

collector = ArtifactCollector(session_id=session.session_id)
collector.record_action_trace("navigate", status="ok", url="https://example.com")
collector.record_js_exception("TypeError", stack="...", url="https://example.com")

# Query
all_artifacts = collector.get_all()
js_errors     = collector.get_by_kind("js_exception")
summary       = collector.summary()
# → {"total": 5, "by_kind": {"action_trace": 3, "js_exception": 2}, ...}
```

Six artifact kinds: `action_trace`, `dom_snapshot`, `js_exception`, `network_request`, `screenshot`, `custom`.

### Replay Engine

Record and replay action sequences for testing, debugging, and regression:

```python
from an_web.tracing.replay import ReplayTrace, ReplayEngine

# Build a trace
trace = ReplayTrace.new(session_id="test-1")
trace.add_step("navigate", {"url": "https://example.com"}, expected_status="ok")
trace.add_step("click",    {"target": "#btn"},              expected_status="ok")
trace.add_step("snapshot", {},                              expected_status="ok")

# Replay it
replay_engine = ReplayEngine()
result = await replay_engine.replay_trace(trace, session)
print(result.succeeded)       # True if all steps passed
print(result.failed_steps)    # details on any failures

# Serialize / deserialize
json_str = trace.to_json()
trace2   = ReplayTrace.from_json(json_str)
```

### Export from ANWebToolInterface

```python
tools = ANWebToolInterface(session)
await tools.navigate("https://example.com")
await tools.click("#btn")

# Automatically built from tool_history
trace_dict = tools.history_as_trace()
```

---

## JavaScript Execution & SPA Support

### Embedded QuickJS Runtime

AN-Web embeds a QuickJS JavaScript engine with a comprehensive host Web API layer:

```python
# Via tool interface
result = await session.act({"tool": "eval_js", "script": "document.title"})
result = await session.act({
    "tool": "eval_js",
    "script": "Array.from(document.querySelectorAll('a')).map(a => a.href)"
})

# Direct runtime access (advanced)
js = session.js_runtime
result = js.eval_safe("1 + 1")          # EvalResult(ok=True, value=2)
await js.drain_microtasks()              # process Promise chains
```

### Host Web API Coverage

The host API layer bridges Python DOM ↔ QuickJS, providing browser-compatible APIs:

| Category | APIs |
|---|---|
| **DOM** | `document.getElementById`, `querySelector`, `querySelectorAll`, `createElement`, `appendChild`, `removeChild`, `insertBefore`, `cloneNode`, `innerHTML`, `textContent`, `getAttribute`, `setAttribute`, `classList`, `style` |
| **Events** | `addEventListener`, `removeEventListener`, `dispatchEvent`, `Event`, `CustomEvent`, `MouseEvent`, `KeyboardEvent`, `FocusEvent`, `InputEvent`, `ErrorEvent` |
| **Timers** | `setTimeout`, `setInterval`, `clearTimeout`, `clearInterval`, `requestAnimationFrame` |
| **Network** | `fetch`, `XMLHttpRequest` |
| **Storage** | `localStorage`, `sessionStorage` |
| **Navigation** | `location`, `history.pushState`, `history.replaceState` |
| **Encoding** | `TextEncoder`, `TextDecoder`, `btoa`, `atob` |
| **Other** | `console`, `JSON`, `Promise`, `MutationObserver`, `IntersectionObserver`, `ResizeObserver`, `performance.now()`, `DOMParser`, `Blob`, `URL`, `URLSearchParams` |

### SPA Framework Support

AN-Web can render modern Single Page Applications:

- **Webpack 5** — Automatic runtime extraction from polyfill bundles
- **React 18** — Full component rendering via host DOM API bridge
- **jQuery / Sizzle** — Compatible selector engine support
- **`defer` scripts** — Correct HTML5 execution order (inline first, deferred after parse)
- **DOMContentLoaded / load** — Proper lifecycle event dispatch

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     AI Tool API                         │
│   dispatch_tool()  ANWebToolInterface  tool_schema.py   │
├───────────────┬──────────────────────┬──────────────────┤
│  Policy Layer │   Tracing Layer      │  Semantic Layer  │
│  rules/sandbox│   artifacts/logs/    │  extractor/      │
│  checker/     │   replay             │  page_type/roles │
│  approvals    │                      │  affordances     │
├───────────────┴──────────────────────┴──────────────────┤
│                   Actions Layer                         │
│  navigate  click  type  submit  extract  scroll  eval_js│
├─────────────────────────────────────────────────────────┤
│              Execution Plane                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │ DOM Core │  │ JS Bridge│  │ Network  │  │Layout  │  │
│  │ nodes/   │  │ QuickJS  │  │ httpx +  │  │Lite    │  │
│  │ selectors│  │ host_api │  │ cookies  │  │hit_test│  │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘  │
├─────────────────────────────────────────────────────────┤
│              Control Plane                              │
│    ANWebEngine   Session   Scheduler   SnapshotManager  │
└─────────────────────────────────────────────────────────┘
```

### Package Structure

```
an_web/
├── core/         # ANWebEngine, Session, Scheduler, SnapshotManager, PageState
├── dom/          # Node/Element/Document, CSS Selectors, Mutation, Semantics
├── js/           # QuickJS bridge, JSRuntime, Host Web API (DOM ↔ QuickJS bridge)
├── net/          # NetworkClient (httpx), CookieJar, ResourceLoader
├── actions/      # navigate, click, type, submit, extract, scroll, eval_js, wait_for
├── layout/       # Visibility, flow inference, hit-testing, LayoutEngine
├── semantic/     # SemanticExtractor, page_type classifier, roles, affordances
├── policy/       # PolicyRules, PolicyChecker, Sandbox, ApprovalManager
├── tracing/      # ArtifactCollector, StructuredLogger, ReplayEngine
├── browser/      # HTML Parser (selectolax + html5lib)
└── api/          # dispatch_tool, ANWebToolInterface, Pydantic models, tool schemas
```

---

## Examples

### Login Flow

```python
from an_web import ANWebEngine
from an_web.api import ANWebToolInterface

async def login():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        tools = ANWebToolInterface(session)

        await tools.navigate("https://example.com/login")

        # Inspect the page
        snap = await tools.snapshot()
        print(f"Page: {snap['page_type']}")  # "login_form"

        # Fill and submit
        await tools.type("#email", "user@example.com")
        await tools.type("#password", "password123")
        await tools.click({"by": "role", "role": "button", "text": "Log in"})

        # Verify
        snap = await tools.snapshot()
        print(f"Logged in: {snap['url']}")
```

### Web Scraping

```python
from an_web import ANWebEngine

async def scrape_headlines():
    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://news.ycombinator.com")

        result = await session.act({
            "tool": "extract",
            "query": "span.titleline > a"
        })

        for item in result["effects"]["results"]:
            print(item["text"])
```

### Multi-Session Parallel Scraping

```python
import asyncio
from an_web import ANWebEngine

async def scrape_url(engine, url):
    session = await engine.create_session()
    await session.navigate(url)
    result = await session.act({"tool": "extract", "query": "h1"})
    await session.close()
    return result["effects"]["results"]

async def main():
    async with ANWebEngine() as engine:
        urls = [
            "https://example.com",
            "https://httpbin.org/html",
            "https://www.python.org",
        ]
        results = await asyncio.gather(*(scrape_url(engine, u) for u in urls))
        for url, data in zip(urls, results):
            print(f"{url}: {data}")
```

### SPA Rendering (React / Webpack)

```python
from an_web import ANWebEngine

async def render_spa():
    async with ANWebEngine() as engine:
        session = await engine.create_session()

        # AN-Web handles: webpack runtime, defer scripts, React rendering
        await session.navigate("https://www.naver.com")
        page = await session.snapshot()

        print(f"Title: {page.title}")
        print(f"Elements: {len(page.semantic_tree.children)}")

        # Extract rendered content
        result = await session.act({"tool": "extract", "query": "a"})
        for link in result["effects"]["results"][:5]:
            print(f"  {link['text']}: {link.get('href', '')}")
```

### Sandboxed Session with Policy

```python
from an_web import ANWebEngine
from an_web.policy.rules import PolicyRules

async def safe_browse():
    policy = PolicyRules.sandboxed(allowed_domains=["example.com"])

    async with ANWebEngine() as engine:
        session = await engine.create_session(policy=policy)

        # Allowed
        await session.navigate("https://example.com")

        # Blocked by policy
        result = await session.act({"tool": "navigate", "url": "https://other.com"})
        print(result["status"])  # "blocked"
```

---

## Testing

```bash
# Run all tests (1524 tests)
pytest

# With coverage
pytest --cov=an_web --cov-report=term-missing

# Specific module
pytest tests/unit/dom/ -v

# Integration tests
pytest tests/integration/ -v
```

**Test Suite (1524 tests):**

| Suite | Count | Covers |
|---|---|---|
| DOM / Selectors / Parser | ~330 | Core DOM tree, CSS selectors, HTML parsing |
| JS Bridge + Runtime + Host API | ~300 | QuickJS eval, Promise drain, host Web API |
| Scheduler / Session / Engine | ~130 | Event loop, navigation, storage, snapshots |
| Actions | ~190 | click, type, submit, extract, scroll, eval_js |
| Layout | ~160 | Visibility, flow, hit-testing |
| Policy + Tracing + API | ~330 | Rules, sandbox, artifacts, logs, replay, dispatch |
| Integration (E2E) | ~46 | Login flow, search & extract, multi-session |

---

## API Reference Summary

### Core

| Class | Import | Description |
|---|---|---|
| `ANWebEngine` | `from an_web import ANWebEngine` | Top-level factory. Async context manager. |
| `Session` | via `engine.create_session()` | Browser tab. Owns cookies, storage, JS runtime. |

### Session Methods

| Method | Returns | Description |
|---|---|---|
| `navigate(url)` | `dict` | Load URL, build DOM, execute JS, settle |
| `snapshot()` | `PageSemantics` | Structured semantic page state (object) |
| `act(tool_call)` | `dict` | Execute any of the 11 tools |
| `execute_script(js)` | `Any` | Direct JavaScript evaluation |
| `back()` | `dict` | Navigate to previous URL |
| `close()` | `None` | Release resources |

### API Layer

| Symbol | Import | Description |
|---|---|---|
| `ANWebToolInterface` | `from an_web.api import ANWebToolInterface` | Typed tool helper methods |
| `dispatch_tool()` | `from an_web.api import dispatch_tool` | Low-level tool dispatch |
| `TOOLS_FOR_CLAUDE` | `from an_web.api import TOOLS_FOR_CLAUDE` | Anthropic tool schemas |
| `TOOLS_FOR_OPENAI` | `from an_web.api import TOOLS_FOR_OPENAI` | OpenAI tool schemas |
| `get_tool(name)` | `from an_web.api import get_tool` | Single tool schema lookup |
| `get_tool_names()` | `from an_web.api import get_tool_names` | List all tool names |

### Policy

| Class | Import | Description |
|---|---|---|
| `PolicyRules` | `from an_web.policy.rules import PolicyRules` | Domain/rate/scope rules |
| `PolicyRules.default()` | | Permissive defaults (120 req/min) |
| `PolicyRules.strict()` | | Conservative (30 req/min, approvals) |
| `PolicyRules.sandboxed(domains)` | | Domain-locked |
| `Sandbox` | `from an_web.policy.sandbox import Sandbox` | Resource limit enforcement |
| `ApprovalManager` | `from an_web.policy.approvals import ApprovalManager` | Human-in-the-loop |

### Data Models

| Class | Description |
|---|---|
| `PageSemantics` | Full page state: page_type, title, url, primary_actions, inputs, blocking_elements, semantic_tree |
| `SemanticNode` | Element in semantic tree: node_id, tag, role, name, value, is_interactive, visible, affordances, children |
| `ActionResult` | Action outcome: status, action, effects, error, recommended_next_actions |

---

## License

Apache-2.0

---

## Contributing

```bash
git clone https://github.com/CocoRoF/an-web
cd an-web
pip install -e ".[dev]"
pytest                    # all 1524 tests should pass
ruff check an_web/        # linting
mypy an_web/              # type checking
```
