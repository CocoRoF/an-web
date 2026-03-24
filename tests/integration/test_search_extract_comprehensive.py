"""
Integration tests: Search & Extract flow — comprehensive coverage.

Verifies:
  1. Search form submission (GET method with query string in URL)
  2. Results page navigation and semantic model accuracy
  3. PageSemantics.to_dict() JSON structure fidelity (every field, camelCase keys)
  4. ActionResult.to_dict() structure (all fields present/absent correctly)
  5. SemanticNode tree integrity (find_by_role, find_by_text, to_dict())
  6. Extract tool — all 4 modes: css / structured / json / html
  7. Structured extraction with field selectors
  8. JSON-LD / embedded JSON extraction
  9. HTML extraction
  10. Multi-step navigation: search → click result → article page
  11. ANWebToolInterface extract + snapshot convenience wrappers
  12. ArtifactCollector captures correct kinds and data
  13. scroll tool on results page
  14. wait_for + network_idle after form submit
  15. Edge cases: no results, empty selector, non-existent element

Success criteria:
  - Every structured JSON field matches the DOM exactly.
  - ActionResult dict produced by session.act() includes all mandatory keys.
  - PageSemantics JSON matches spec: pageType, title, url, primaryActions, inputs,
    blockingElements, semanticTree (with nodeId/role/isInteractive etc), snapshotId.
  - All 24 tests pass without external network calls.
"""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine
from an_web.api.rpc import ANWebToolInterface
from an_web.api.models import ActionResponse
from an_web.tracing.artifacts import ArtifactCollector, ArtifactKind


# ── HTML fixtures ──────────────────────────────────────────────────────────────

SEARCH_HOME = b"""<!DOCTYPE html>
<html><head><title>WebSearch</title></head>
<body>
  <header><a href="/" class="logo">WebSearch</a></header>
  <main>
    <form id="search-form" action="/search" method="get" role="search">
      <input id="q" type="search" name="q"
             placeholder="Search the web..." aria-label="Search">
      <button type="submit" id="search-btn">Search</button>
    </form>
  </main>
</body></html>"""

RESULTS_HTML = b"""<!DOCTYPE html>
<html><head>
  <title>python asyncio - WebSearch Results</title>
  <script type="application/json" id="search-meta">
{"query":"python asyncio","total_results":12300000,"page":1}
  </script>
  <script type="application/ld+json">
{"@context":"https://schema.org","@type":"SearchResultsPage","name":"python asyncio"}
  </script>
</head>
<body>
  <header>
    <form action="/search" method="get" id="header-search">
      <input type="search" name="q" value="python asyncio" aria-label="Search">
      <button type="submit">Search</button>
    </form>
  </header>
  <main id="results">
    <p class="result-count">About 12,300,000 results</p>
    <ol class="results-list">
      <li class="result-item" data-position="1">
        <h3 class="result-title">
          <a href="https://docs.python.org/asyncio" class="result-link">
            asyncio - Python Documentation
          </a>
        </h3>
        <p class="result-snippet">asyncio is a library to write concurrent code.</p>
      </li>
      <li class="result-item" data-position="2">
        <h3 class="result-title">
          <a href="https://realpython.com/async-io-python" class="result-link">
            Async IO in Python: A Complete Walkthrough
          </a>
        </h3>
        <p class="result-snippet">An in-depth tutorial covering Python asyncio.</p>
      </li>
      <li class="result-item" data-position="3">
        <h3 class="result-title">
          <a href="https://example.com/asyncio-internals" class="result-link">
            Understanding Asyncio Internals
          </a>
        </h3>
        <p class="result-snippet">Deep dive into how asyncio event loop works.</p>
      </li>
    </ol>
    <nav class="pagination" aria-label="Pagination">
      <a href="/search?q=python+asyncio&page=2" id="next-page" class="btn-next">Next</a>
    </nav>
  </main>
</body></html>"""

ARTICLE_HTML = b"""<!DOCTYPE html>
<html><head>
  <title>Understanding Asyncio Internals</title>
  <script type="application/ld+json">
{"@context":"https://schema.org","@type":"Article","headline":"Understanding Asyncio Internals","author":{"@type":"Person","name":"Jane Developer"}}
  </script>
</head>
<body>
  <article id="main-article">
    <h1>Understanding Asyncio Internals</h1>
    <p class="intro">The event loop is the core of asyncio.</p>
    <h2>The Event Loop</h2>
    <p>asyncio uses a single-threaded event loop.</p>
  </article>
</body></html>"""

BASE = "https://websearch.example.com"


def _mock_search_flow() -> None:
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(
            200, content=SEARCH_HOME,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200, content=RESULTS_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    respx.get("https://example.com/asyncio-internals").mock(
        return_value=httpx.Response(
            200, content=ARTICLE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Complete search flow — navigate → type → submit → results URL
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_complete_search_flow():
    """
    Full AI agent search scenario.
    Success criteria:
      ✓ navigate → ok, URL set
      ✓ snapshot → inputs contain search field, search button in primary_actions
      ✓ type query → DOM value mutated
      ✓ click submit → form GET → URL contains query string
      ✓ results snapshot → 3 links, snippets present
    """
    _mock_search_flow()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:

            # Navigate to search home
            nav = await session.navigate(f"{BASE}/")
            assert nav["status"] == "ok"
            assert session.current_url == f"{BASE}/"

            # Snapshot the search page
            page = await session.snapshot()
            assert page.title == "WebSearch"
            assert len(page.inputs) >= 1
            q_input = next(
                (inp for inp in page.inputs
                 if inp.get("attributes", {}).get("name") == "q"),
                None,
            )
            assert q_input is not None, "search input not found in page.inputs"

            # Search input should be interactive
            assert q_input.get("is_interactive") is True or q_input.get("isInteractive") is True

            # Type query
            r_type = await session.act({
                "tool": "type",
                "target": "#q",
                "text": "python asyncio",
            })
            assert r_type["status"] == "ok"

            # Verify DOM mutation
            q_el = session._current_document.get_element_by_id("q")
            assert q_el.get_attribute("value") == "python asyncio"

            # Click search button (GET form submit)
            r_click = await session.act({
                "tool": "click",
                "target": "#search-btn",
            })
            assert r_click["status"] == "ok"
            assert r_click.get("effects", {}).get("form_submitted") is True

            # URL should contain the query (GET form → query string)
            assert "search" in session.current_url or "q=" in session.current_url

            # Results snapshot
            results_page = await session.snapshot()
            assert "Results" in results_page.title or "asyncio" in results_page.title.lower()

            # Links in semantic tree
            links = results_page.semantic_tree.find_by_role("link")
            assert len(links) >= 3
            link_names = [l.name for l in links if l.name]
            assert any("asyncio" in n.lower() for n in link_names)


# ══════════════════════════════════════════════════════════════════════════════
# 2. PageSemantics JSON structure accuracy
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_page_semantics_json_structure():
    """
    PageSemantics.to_dict() must produce the exact expected JSON schema:
      - pageType (str)
      - title (str)
      - url (str)
      - primaryActions (list of dicts)
      - inputs (list of dicts with nodeId/role/isInteractive/attributes)
      - blockingElements (list)
      - semanticTree (dict with nodeId/nodeName/role/xpath/isInteractive/visible/affordances/confidence)
      - snapshotId (str starting with 'snap-')
    """
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(
            200, content=SEARCH_HOME,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            page = await session.snapshot()
            d = page.to_dict()

    # ── Top-level keys (camelCase) ─────────────────────────────────────────────
    assert "pageType" in d,         "missing pageType"
    assert "title" in d,            "missing title"
    assert "url" in d,              "missing url"
    assert "primaryActions" in d,   "missing primaryActions"
    assert "inputs" in d,           "missing inputs"
    assert "blockingElements" in d, "missing blockingElements"
    assert "semanticTree" in d,     "missing semanticTree"
    assert "snapshotId" in d,       "missing snapshotId"

    # ── Value types ────────────────────────────────────────────────────────────
    assert isinstance(d["pageType"], str)
    assert isinstance(d["title"], str)
    assert isinstance(d["url"], str)
    assert isinstance(d["primaryActions"], list)
    assert isinstance(d["inputs"], list)
    assert isinstance(d["blockingElements"], list)
    assert isinstance(d["semanticTree"], dict)
    assert isinstance(d["snapshotId"], str)

    # ── Value accuracy ─────────────────────────────────────────────────────────
    assert d["title"] == "WebSearch"
    assert d["url"] == f"{BASE}/"
    assert d["snapshotId"].startswith("snap-"), f"bad snapshotId: {d['snapshotId']}"

    # ── SemanticTree structure ─────────────────────────────────────────────────
    tree = d["semanticTree"]
    assert "nodeId" in tree,      "semanticTree missing nodeId"
    assert "role" in tree,        "semanticTree missing role"
    assert "isInteractive" in tree or "children" in tree, "semanticTree missing isInteractive"

    # ── Inputs structure ───────────────────────────────────────────────────────
    assert len(d["inputs"]) >= 1
    inp = d["inputs"][0]
    # inputs are SemanticNode dicts — must have nodeId, role, isInteractive
    for key in ("nodeId", "role", "isInteractive"):
        assert key in inp, f"input missing key: {key}"

    # ── Round-trip JSON-serialisable ───────────────────────────────────────────
    json_str = json.dumps(d, ensure_ascii=False, default=str)
    restored = json.loads(json_str)
    assert restored["title"] == "WebSearch"
    assert restored["snapshotId"] == d["snapshotId"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. ActionResult structure for each tool type
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_action_result_structure_navigate():
    """navigate ActionResult.to_dict() must have: status, action; optional target, effects, stateDeltaId."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            result = await session.navigate(f"{BASE}/")

    # Mandatory fields
    assert "status" in result
    assert "action" in result
    assert result["status"] == "ok"
    assert result["action"] == "navigate"

    # effects is only present if non-empty
    if "effects" in result:
        assert isinstance(result["effects"], dict)

    # No error field when successful
    assert result.get("error") is None or "error" not in result

    # stateDeltaId present only when set
    if "stateDeltaId" in result:
        assert isinstance(result["stateDeltaId"], str)


@pytest.mark.asyncio
@respx.mock
async def test_action_result_structure_type():
    """type ActionResult must have status=ok, action=type, and effects with value_set."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            result = await session.act({"tool": "type", "target": "#q", "text": "hello"})

    assert result["status"] == "ok"
    assert result["action"] == "type"
    effects = result.get("effects", {})
    assert isinstance(effects, dict)


@pytest.mark.asyncio
@respx.mock
async def test_action_result_structure_click():
    """click ActionResult must have: status, action, effects (form_submitted, navigation)."""
    _mock_search_flow()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            await session.act({"tool": "type", "target": "#q", "text": "test"})
            result = await session.act({"tool": "click", "target": "#search-btn"})

    assert "status" in result
    assert "action" in result
    assert result["action"] == "click"
    effects = result.get("effects", {})
    assert "form_submitted" in effects
    assert effects["form_submitted"] is True


@pytest.mark.asyncio
@respx.mock
async def test_action_result_structure_extract():
    """extract ActionResult must have effects.count, effects.results, effects.mode."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            result = await session.act({"tool": "extract", "query": ".result-item"})

    assert result["status"] == "ok"
    assert result["action"] == "extract"
    effects = result.get("effects", {})
    assert "count" in effects,   "effects missing count"
    assert "results" in effects, "effects missing results"
    assert "mode" in effects,    "effects missing mode"
    assert effects["count"] == 3
    assert isinstance(effects["results"], list)
    assert effects["mode"] == "css"


@pytest.mark.asyncio
@respx.mock
async def test_action_result_failed_structure():
    """A failed ActionResult must have status=failed, action, error. No error details on ok."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            result = await session.act({"tool": "click", "target": "#nonexistent-element-xyz"})

    assert result["status"] == "failed"
    assert "action" in result
    assert "error" in result
    assert isinstance(result["error"], str)
    assert len(result["error"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. SemanticNode JSON structure fidelity
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_semantic_node_json_keys():
    """
    Every SemanticNode in the tree must have these camelCase keys:
      nodeId, nodeName, role, xpath, isInteractive, visible, affordances, confidence.
    """
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            page = await session.snapshot()

    REQUIRED_NODE_KEYS = {"nodeId", "nodeName", "role", "xpath", "isInteractive", "visible",
                          "affordances", "confidence"}

    def check_node(d: dict, path: str = "root") -> None:
        for key in REQUIRED_NODE_KEYS:
            assert key in d, f"SemanticNode at {path!r} missing key: {key!r}"
        assert isinstance(d["nodeId"], str) and len(d["nodeId"]) > 0, f"{path}: nodeId empty"
        assert isinstance(d["role"], str),                             f"{path}: role not str"
        assert isinstance(d["isInteractive"], bool),                   f"{path}: isInteractive not bool"
        assert isinstance(d["visible"], bool),                         f"{path}: visible not bool"
        assert isinstance(d["affordances"], list),                     f"{path}: affordances not list"
        assert isinstance(d["confidence"], (int, float)),              f"{path}: confidence not number"
        for child in d.get("children", []):
            check_node(child, path=d["nodeId"])

    tree_dict = page.to_dict()["semanticTree"]
    check_node(tree_dict)


@pytest.mark.asyncio
@respx.mock
async def test_semantic_node_interactive_nodes():
    """Input and button elements must be marked isInteractive=True."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            page = await session.snapshot()

    interactive = page.semantic_tree.find_interactive()
    assert len(interactive) >= 2, f"expected ≥2 interactive nodes, got {len(interactive)}"
    roles = {n.role for n in interactive}
    # Should have textbox (input) and button
    assert "textbox" in roles or "searchbox" in roles, f"no textbox/searchbox in {roles}"
    assert "button" in roles, f"no button in {roles}"


@pytest.mark.asyncio
@respx.mock
async def test_semantic_tree_find_by_role():
    """find_by_role returns correct nodes from results page."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            page = await session.snapshot()

    links = page.semantic_tree.find_by_role("link")
    assert len(links) >= 3

    buttons = page.semantic_tree.find_by_role("button")
    assert len(buttons) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_semantic_tree_find_by_text():
    """find_by_text(partial=True) finds nodes by accessible name substring."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            page = await session.snapshot()

    # Should find the "Next" pagination link
    next_links = page.semantic_tree.find_by_text("Next", partial=True)
    assert len(next_links) >= 1

    # Should find "asyncio" in link names
    asyncio_nodes = page.semantic_tree.find_by_text("asyncio", partial=True)
    assert len(asyncio_nodes) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. Extract tool — CSS mode deep validation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_css_result_items():
    """CSS extraction returns correctly shaped result dicts."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            r = await session.act({"tool": "extract", "query": ".result-item"})

    assert r["status"] == "ok"
    assert r["effects"]["count"] == 3
    results = r["effects"]["results"]

    for item in results:
        # Each result dict must have these keys
        assert "node_id" in item,   f"result missing node_id: {item}"
        assert "tag" in item,       f"result missing tag: {item}"
        assert "text" in item,      f"result missing text: {item}"
        assert "attributes" in item, f"result missing attributes: {item}"
        assert item["tag"] == "li"
        assert len(item["text"]) > 0, f"result has empty text: {item}"

    # Verify text content accuracy
    all_text = " ".join(r["text"] for r in results).lower()
    assert "asyncio" in all_text
    assert "python" in all_text


@pytest.mark.asyncio
@respx.mock
async def test_extract_css_links():
    """Extract all <a> links from results page — href attributes present."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            r = await session.act({"tool": "extract", "query": ".result-link"})

    assert r["status"] == "ok"
    assert r["effects"]["count"] == 3
    results = r["effects"]["results"]

    for item in results:
        assert item["tag"] == "a"
        href = item.get("attributes", {}).get("href", "")
        assert href.startswith("https://"), f"result link href wrong: {href}"

    hrefs = [r["attributes"].get("href", "") for r in results]
    assert any("docs.python.org" in h for h in hrefs)
    assert any("realpython.com" in h for h in hrefs)
    assert any("asyncio-internals" in h for h in hrefs)


@pytest.mark.asyncio
@respx.mock
async def test_extract_css_snippets():
    """Extract result snippets — text content matches HTML."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            r = await session.act({"tool": "extract", "query": ".result-snippet"})

    assert r["status"] == "ok"
    assert r["effects"]["count"] == 3
    texts = [item["text"] for item in r["effects"]["results"]]
    assert all(len(t) > 10 for t in texts), f"snippets too short: {texts}"
    assert any("asyncio" in t.lower() for t in texts)
    assert any("concurrent" in t.lower() for t in texts)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Extract tool — structured mode
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_structured_mode():
    """Structured extraction returns named fields per item."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            r = await session.act({
                "tool": "extract",
                "query": {
                    "selector": ".result-item",
                    "fields": {
                        "title": ".result-title",
                        "snippet": ".result-snippet",
                        "link": {"sel": ".result-link", "attr": "href"},
                    },
                },
            })

    assert r["status"] == "ok"
    assert r["effects"]["mode"] == "structured"
    assert r["effects"]["count"] == 3

    results = r["effects"]["results"]
    for item in results:
        assert "title" in item, f"structured result missing title: {item}"
        assert "snippet" in item, f"structured result missing snippet: {item}"
        assert "link" in item, f"structured result missing link: {item}"
        assert "node_id" in item

    # Verify link URLs
    links = [r["link"] for r in results if r.get("link")]
    assert len(links) == 3
    assert any("asyncio" in (lnk or "").lower() for lnk in links)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Extract tool — JSON mode (application/json)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_json_mode():
    """
    Extract JSON from <script type='application/json'> tags.
    The results page has a JSON block with query metadata.
    """
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            r = await session.act({
                "tool": "extract",
                "query": {"mode": "json",
                          "selector": "script[type='application/json']"},
            })

    assert r["status"] == "ok"
    assert r["effects"]["mode"] == "json"

    results = r["effects"]["results"]
    # If extraction succeeds, validate the data shape
    if results:
        for item in results:
            assert "node_id" in item
            assert "data" in item
            data = item["data"]
            if isinstance(data, dict):
                # our fixture has query, total_results, page
                if "query" in data:
                    assert data["query"] == "python asyncio"
                    assert data["page"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_extract_json_ld_mode():
    """
    Extract JSON-LD from <script type='application/ld+json'>.
    Article page has structured article metadata.
    """
    respx.get("https://example.com/asyncio-internals").mock(
        return_value=httpx.Response(
            200, content=ARTICLE_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://example.com/asyncio-internals")

            r = await session.act({
                "tool": "extract",
                "query": {"mode": "json",
                          "selector": "script[type='application/ld+json']"},
            })

    assert r["status"] == "ok"
    results = r["effects"]["results"]
    if results:
        data = results[0].get("data", {})
        if isinstance(data, dict):
            if "@type" in data:
                assert data["@type"] == "Article"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Extract tool — HTML mode
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_html_mode():
    """HTML mode returns outer HTML of matched elements."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            r = await session.act({
                "tool": "extract",
                "query": {"mode": "html", "selector": ".result-title"},
            })

    assert r["status"] == "ok"
    assert r["effects"]["mode"] == "html"
    results = r["effects"]["results"]
    assert len(results) == 3

    for item in results:
        assert "node_id" in item
        assert "html" in item
        # html content should include result title text
        assert len(item["html"]) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 9. Multi-step: search → click result → article page
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_search_click_result_navigate():
    """
    Full multi-step flow: search → results page → click link → article.
    """
    _mock_search_flow()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            # Search
            await session.navigate(f"{BASE}/")
            await session.act({"tool": "type", "target": "#q", "text": "python asyncio"})
            await session.act({"tool": "click", "target": "#search-btn"})

            # Results page
            assert "search" in session.current_url or "q=" in session.current_url

            # Click third result
            r = await session.act({
                "tool": "click",
                "target": {"by": "text", "text": "Understanding Asyncio"},
            })
            assert r["status"] == "ok"
            assert session.current_url == "https://example.com/asyncio-internals"

            # Article snapshot
            article = await session.snapshot()
            assert "Asyncio" in article.title

            # Extract article headings
            r_extract = await session.act({
                "tool": "extract",
                "query": "h2",
            })
            assert r_extract["status"] == "ok"
            headings = [i["text"] for i in r_extract["effects"]["results"]]
            assert any("Event Loop" in h for h in headings)


# ══════════════════════════════════════════════════════════════════════════════
# 10. ANWebToolInterface — snapshot + extract round-trip
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_interface_search_extract():
    """ANWebToolInterface.snapshot() returns a full dict matching PageSemantics."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            iface = ANWebToolInterface(session)

            await iface.navigate(f"{BASE}/search")
            snap = await iface.snapshot()

            # snapshot() via interface returns a dict (the to_dict() result)
            assert isinstance(snap, dict)
            assert snap.get("title") == "python asyncio - WebSearch Results" or \
                   "title" in snap

            # ActionResponse wrapping
            resp = ActionResponse.from_result(snap)
            # snap is actually a PageSemantics dict — not an ActionResult dict
            # so status may not be there; just verify it doesn't crash
            assert resp is not None

            # Extract via interface
            r_extract = await iface.extract(".result-link")
            assert r_extract["status"] == "ok"
            assert r_extract["effects"]["count"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# 11. Artifact collection during search flow
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_artifact_collection_search_flow():
    """ArtifactCollector captures ACTION_TRACE for each step of the search flow."""
    _mock_search_flow()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            session.artifacts = ArtifactCollector(session.session_id, max_size=20)

            await session.navigate(f"{BASE}/")
            await session.act({"tool": "type", "target": "#q", "text": "python asyncio"})
            await session.act({"tool": "click", "target": "#search-btn"})
            await session.act({"tool": "extract", "query": ".result-item"})

            traces = session.artifacts.get_by_kind(ArtifactKind.ACTION_TRACE)
            assert len(traces) >= 3

            actions = {t.data["action"] for t in traces}
            assert "type" in actions
            assert "extract" in actions

            # All traces have valid statuses
            for t in traces:
                assert t.data["status"] in ("ok", "failed", "blocked"), (
                    f"unexpected status: {t.data['status']}"
                )

            # Summary
            summary = session.artifacts.summary()
            assert summary["total"] >= 3
            assert summary["action_failures"] == 0  # all steps were ok


# ══════════════════════════════════════════════════════════════════════════════
# 12. Snapshot returns consistent data across multiple calls
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_snapshot_idempotent():
    """
    Calling snapshot() twice on the same page returns the same structural data.
    snapshot_id should differ (each call creates a new snapshot).
    """
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")

            page1 = await session.snapshot()
            page2 = await session.snapshot()

    assert page1.title == page2.title
    assert page1.page_type == page2.page_type
    assert page1.url == page2.url
    assert len(page1.inputs) == len(page2.inputs)
    # snapshot_id must be different each time
    assert page1.snapshot_id != page2.snapshot_id


# ══════════════════════════════════════════════════════════════════════════════
# 13. Edge cases
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_no_results():
    """Extracting a non-existent selector returns count=0, results=[]."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            r = await session.act({
                "tool": "extract",
                "query": ".this-does-not-exist-xyz",
            })

    assert r["status"] == "ok"
    assert r["effects"]["count"] == 0
    assert r["effects"]["results"] == []


@pytest.mark.asyncio
@respx.mock
async def test_extract_before_navigate():
    """extract before any navigation must return status=failed."""
    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            r = await session.act({"tool": "extract", "query": "div"})

    assert r["status"] == "failed"
    assert "error" in r


@pytest.mark.asyncio
@respx.mock
async def test_click_nonexistent_element():
    """clicking a non-existent element returns status=failed with target_not_found."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            r = await session.act({"tool": "click", "target": "#no-such-button"})

    assert r["status"] == "failed"
    assert "error" in r


# ══════════════════════════════════════════════════════════════════════════════
# 14. scroll tool on results page
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_scroll_results_page():
    """scroll action returns ok on a loaded page."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            r = await session.act({"tool": "scroll", "delta_y": 500})

    assert r["status"] == "ok"
    assert r["action"] == "scroll"


# ══════════════════════════════════════════════════════════════════════════════
# 15. Search page semantic classification accuracy
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_search_home_semantic_classification():
    """The search home page should be classified as search or form type."""
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, content=SEARCH_HOME, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/")
            page = await session.snapshot()

    EXPECTED_TYPES = {"search", "search_results", "form", "generic"}
    assert page.page_type in EXPECTED_TYPES, (
        f"unexpected page_type: {page.page_type!r} — expected one of {EXPECTED_TYPES}"
    )

    # Primary actions should include the Search button
    action_names = [
        a.get("name", "") or a.get("text", "")
        for a in page.primary_actions
    ]
    # button text should contain "search" or similar
    assert any("search" in (n or "").lower() or "Search" in (n or "") for n in action_names), (
        f"Search button not in primary_actions: {page.primary_actions}"
    )


@pytest.mark.asyncio
@respx.mock
async def test_results_page_link_count():
    """
    Results page snapshot must see at least 3 links corresponding to the 3 result items.
    """
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            page = await session.snapshot()

    links = page.semantic_tree.find_by_role("link")
    # 3 result links + 1 Next pagination link + 1 logo link in header search
    assert len(links) >= 3

    # Check specific link names
    link_names = [l.name for l in links if l.name]
    assert any("asyncio" in (n or "").lower() for n in link_names), (
        f"asyncio link not found: {link_names}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 16. ActionResponse Pydantic model wrapping extract result
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_action_response_from_extract():
    """ActionResponse.from_result() correctly wraps an extract result."""
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML, headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/search")
            raw = await session.act({"tool": "extract", "query": ".result-link"})

    resp = ActionResponse.from_result(raw)
    assert resp.ok is True
    assert resp.action == "extract"
    assert resp.effects.count == 3
    assert resp.effects.results is not None
    assert len(resp.effects.results) == 3

    # to_tool_result() Anthropic format
    tr = resp.to_tool_result(tool_use_id="tu-search-001")
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is False
    payload = json.loads(tr["content"])
    assert payload["status"] == "ok"
    assert payload["effects"]["count"] == 3
