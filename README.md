# AN-Web — AI-Native Web Browser Engine

AN-Web is a Python-native headless browser engine purpose-built for AI agents.
Instead of rendering pixels for human eyes, it executes the web as an **actionable state machine** —
every page becomes a structured semantic graph that an agent can reason over and act upon.

```
Navigate → Snapshot → Decide → Act → Observe → Repeat
```

## Why AN-Web?

Standard headless browsers (Playwright, Puppeteer) were designed for human-driven testing.
AN-Web is designed from scratch for the AI loop:

| Concern | Traditional headless | AN-Web |
|---|---|---|
| Primary output | Screenshots / DOM strings | `PageSemantics` — structured world model |
| JS engine | V8 (full Chromium) | QuickJS (lightweight, embeddable) |
| Latency | 500 ms+ cold start | < 50 ms per action |
| Memory | 300–800 MB | ~30 MB |
| Action targeting | CSS selectors / XPath | Semantic: `{"by": "role", "role": "button", "text": "Sign In"}` |
| Policy & safety | None | Built-in domain rules, rate limits, approval flows |
| Observability | External tracing | First-class `ArtifactCollector`, `StructuredLogger`, `ReplayEngine` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     AI Tool API                         │
│   dispatch_tool()  ANWebToolInterface  tool_schema.py   │
├───────────────┬──────────────────────┬──────────────────┤
│  Policy Layer │   Tracing Layer       │  Semantic Layer  │
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
├── core/         # ANWebEngine, Session, Scheduler, SnapshotManager
├── dom/          # Nodes, Document, CSS Selectors, Semantics models
├── js/           # QuickJS bridge, JSRuntime, Host Web API (~1300 lines of JS polyfills)
├── net/          # NetworkClient (httpx), CookieJar, ResourceLoader
├── actions/      # navigate, click, type, submit, extract, scroll, eval_js, wait_for
├── layout/       # Visibility, flow inference, hit-testing, LayoutEngine
├── semantic/     # SemanticExtractor, page_type classifier, roles, affordances
├── policy/       # PolicyRules, PolicyChecker, sandbox, approval flows
├── tracing/      # ArtifactCollector, StructuredLogger, ReplayEngine
└── api/          # dispatch_tool, ANWebToolInterface, Pydantic models, tool schemas
```

---

## Installation

**Requirements:** Python 3.12+

```bash
# Install from source
git clone https://github.com/CocoRoF/an-web
cd an-web
pip install -e .

# With dev tools (pytest, ruff, mypy)
pip install -e ".[dev]"
```

**Dependencies:**
- `httpx` — async HTTP client
- `selectolax` (Lexbor) — fast HTML parser
- `html5lib` — fallback spec-compliant parser
- `pydantic` — request/response validation
- `quickjs-py` — embedded QuickJS runtime
- `cssselect` — CSS selector parsing

---

## Quick Start

### Basic Navigation + Snapshot

```python
import asyncio
from an_web.core.engine import ANWebEngine

async def main():
    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://example.com")
            semantics = await session.snapshot()

            print(f"Page type: {semantics.page_type}")
            print(f"Title: {semantics.title}")
            print(f"Interactive elements: {len(semantics.primary_actions)}")
            print(f"Inputs: {len(semantics.inputs)}")

asyncio.run(main())
```

### AI Tool Interface (Recommended)

The `ANWebToolInterface` exposes AN-Web as a set of AI-callable tools compatible with
Anthropic Claude and OpenAI function-calling formats.

```python
from an_web.api.rpc import ANWebToolInterface
from an_web.core.engine import ANWebEngine

async def run_agent_loop():
    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            iface = ANWebToolInterface(session)

            # Flat format
            result = await iface.run({"tool": "navigate", "url": "https://example.com"})

            # Anthropic tool_use format
            result = await iface.run({
                "name": "click",
                "input": {"target": {"by": "role", "role": "button", "text": "Sign In"}}
            })

            # Convenience wrappers
            await iface.navigate("https://example.com/login")
            await iface.snapshot()
            await iface.type("#email", "user@example.com")
            await iface.type("#password", "secret")
            await iface.click({"by": "role", "role": "button", "text": "Log in"})

            # Export full session as a ReplayTrace
            trace = iface.history_as_trace()
            print(trace.to_json())
```

### dispatch_tool — Low-Level API

```python
from an_web.api.rpc import dispatch_tool

# All 11 tools available:
result = await dispatch_tool(session, {"tool": "navigate",  "url": "https://..."})
result = await dispatch_tool(session, {"tool": "snapshot"})
result = await dispatch_tool(session, {"tool": "click",     "target": "#submit-btn"})
result = await dispatch_tool(session, {"tool": "type",      "target": "#search", "text": "asyncio"})
result = await dispatch_tool(session, {"tool": "clear",     "target": "#search"})
result = await dispatch_tool(session, {"tool": "select",    "target": "#country", "value": "KR"})
result = await dispatch_tool(session, {"tool": "submit",    "target": "form"})
result = await dispatch_tool(session, {"tool": "extract",   "query": "li.result"})
result = await dispatch_tool(session, {"tool": "scroll",    "direction": "down", "amount": 500})
result = await dispatch_tool(session, {"tool": "wait_for",  "condition": "dom_stable"})
result = await dispatch_tool(session, {"tool": "eval_js",   "script": "document.title"})

print(result["status"])   # "ok" | "failed" | "blocked"
print(result["effects"])  # tool-specific output dict
```

### Semantic Targeting

AN-Web supports five target resolution strategies for `click`, `type`, `submit`:

```python
# 1. CSS selector (string)
{"target": "#login-btn"}
{"target": "button[type=submit]"}

# 2. Semantic role + text
{"target": {"by": "role", "role": "button", "text": "Sign In"}}
{"target": {"by": "role", "role": "textbox", "name": "Email"}}

# 3. Visible text search
{"target": {"by": "text", "text": "Forgot password?"}}

# 4. Internal node_id (from snapshot)
{"target": {"by": "node_id", "node_id": "el-42"}}

# 5. General semantic query
{"target": {"by": "semantic", "text": "submit"}}
```

### Data Extraction

```python
# CSS mode — list of matching elements
await dispatch_tool(session, {"tool": "extract", "query": "li.result-item"})
# → {"effects": {"count": 3, "results": [{"node_id": ..., "tag": "li", "text": ...}]}}

# Structured mode — named fields per item
await dispatch_tool(session, {
    "tool": "extract",
    "query": {
        "selector": ".result-item",
        "fields": {
            "title":   ".result-title",
            "snippet": ".result-snippet",
            "url":     {"sel": ".result-link", "attr": "href"},
        }
    }
})

# JSON mode — parse <script type="application/json">
await dispatch_tool(session, {
    "tool": "extract",
    "query": {"mode": "json", "selector": "script[type='application/ld+json']"}
})

# HTML mode — raw outer HTML
await dispatch_tool(session, {
    "tool": "extract",
    "query": {"mode": "html", "selector": "article.main"}
})
```

---

## Policy & Safety

```python
from an_web.policy.rules import PolicyRules, NavigationScope
from an_web.core.engine import ANWebEngine

policy = PolicyRules(
    allowed_domains=["example.com", "api.example.com"],
    denied_domains=["evil.com"],
    navigation_scope=NavigationScope.SUBDOMAIN,
    max_requests_per_minute=60,
    require_approval_for=["submit", "navigate"],
)

async with ANWebEngine() as engine:
    async with await engine.create_session(policy=policy) as session:
        # Navigation to denied domain returns status="blocked"
        result = await dispatch_tool(session, {"tool": "navigate", "url": "https://evil.com"})
        assert result["status"] == "blocked"
```

---

## Tracing & Replay

```python
from an_web.tracing.artifacts import ArtifactCollector, ArtifactKind
from an_web.tracing.replay import ReplayTrace, ReplayEngine

# Collect artifacts during a session
collector = ArtifactCollector(session_id=session.session_id)
collector.record_action_trace("navigate", {"url": url}, {"status": "ok"})
collector.record_dom_snapshot(session._current_document)

summary = collector.summary()
# → {"total": 2, "by_kind": {"action_trace": 1, "dom_snapshot": 1}, ...}

# Build a replay trace and re-run it
trace = ReplayTrace.new(session_id=session.session_id)
trace.add_step("navigate", {"url": "https://example.com"}, expected_status="ok")
trace.add_step("click",    {"target": "#btn"}, expected_status="ok")

replay_engine = ReplayEngine()
result = await replay_engine.replay_trace(trace, session)
print(result.succeeded)      # True / False
print(result.failed_steps)   # list of (step_id, error) pairs

# Persist and restore
json_str = trace.to_json()
trace2 = ReplayTrace.from_json(json_str)
```

---

## AI Tool Schemas

AN-Web ships tool schemas in both Anthropic and OpenAI formats:

```python
from an_web.api.tool_schema import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI, get_tool

# Pass directly to Claude API
response = anthropic.messages.create(
    model="claude-opus-4-6",
    tools=TOOLS_FOR_CLAUDE,
    messages=[{"role": "user", "content": "Log in to example.com"}],
)

# Or OpenAI / compatible APIs
response = openai.chat.completions.create(
    model="gpt-4o",
    tools=TOOLS_FOR_OPENAI,
    messages=[...],
)

# Look up a single tool schema
navigate_schema = get_tool("navigate")
```

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=an_web --cov-report=term-missing

# Run a specific module
pytest tests/unit/test_session.py -v

# Run integration tests
pytest tests/integration/ -v
```

**Test matrix (1461 tests):**

| Suite | Count | Coverage |
|---|---|---|
| DOM / Selectors / Parser | ~330 | core DOM, CSS selector engine, HTML parsing |
| JS Bridge + Runtime + Host API | ~300 | QuickJS eval, Promise drain, 25 host callbacks |
| Scheduler / Session / Engine | ~130 | Event loop, navigate, storage, snapshots |
| Actions | ~190 | click/type/submit/extract/scroll/eval_js |
| Layout-Lite | ~160 | visibility, flow, hit-test, LayoutEngine |
| Policy + Tracing + API | ~330 | rules, sandbox, artifacts, logs, replay, dispatch_tool |
| Integration (E2E) | ~46 | login flow, search & extract, multi-session |

---

## Core Data Models

### `PageSemantics` — the AI world model

```python
@dataclass
class PageSemantics:
    page_type: str          # "login_form" | "search" | "product_detail" | ...
    title: str
    url: str
    primary_actions: list[SemanticNode]   # buttons, links, submits
    inputs: list[SemanticNode]            # form fields
    blocking_elements: list[SemanticNode] # modals, dialogs
    semantic_tree: SemanticNode           # full tree root
    snapshot_id: str
```

### `SemanticNode` — individual element

```python
@dataclass
class SemanticNode:
    node_id: str
    tag: str
    role: str           # ARIA role
    name: str           # accessible name
    value: str          # current value (inputs)
    xpath: str
    is_interactive: bool
    visible: bool
    attributes: dict[str, str]
    children: list[SemanticNode]
    affordances: list[str]  # ["clickable", "typeable", "submittable"]
```

### `ActionResult` — every action returns this

```python
@dataclass
class ActionResult:
    status: str          # "ok" | "failed" | "blocked"
    action: str          # tool name
    target: str | None
    effects: dict        # tool-specific output
    state_delta_id: str  # snapshot diff reference
    error: str | None
    recommended_next_actions: list[dict]
```

---

## License

Apache-2.0
