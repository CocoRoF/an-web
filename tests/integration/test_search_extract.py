"""
Integration test: Search & Extract flow

Simulates an AI agent:
  1. Navigate to a search engine page
  2. Inspect page semantics
  3. Type a search query
  4. Submit the search form
  5. Extract results from the results page
  6. Verify extracted data structure

All network calls mocked.
"""
from __future__ import annotations

import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine


SEARCH_HOME_HTML = b"""
<!DOCTYPE html>
<html>
<head><title>WebSearch</title></head>
<body>
  <header>
    <a href="/" class="logo">WebSearch</a>
  </header>
  <main>
    <form id="search-form" action="/search" method="get" role="search">
      <input
        id="q"
        type="search"
        name="q"
        placeholder="Search the web..."
        aria-label="Search"
        autocomplete="off"
      >
      <button type="submit" id="search-btn" class="btn-search">Search</button>
    </form>
  </main>
</body>
</html>
"""

RESULTS_HTML = b"""
<!DOCTYPE html>
<html>
<head><title>python asyncio - WebSearch Results</title></head>
<body>
  <header>
    <form action="/search" method="get" role="search">
      <input type="search" name="q" value="python asyncio">
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
        <p class="result-snippet">
          asyncio is a library to write concurrent code using the async/await syntax.
        </p>
      </li>
      <li class="result-item" data-position="2">
        <h3 class="result-title">
          <a href="https://realpython.com/async-io-python" class="result-link">
            Async IO in Python: A Complete Walkthrough
          </a>
        </h3>
        <p class="result-snippet">
          An in-depth tutorial covering Python asyncio from the ground up.
        </p>
      </li>
      <li class="result-item" data-position="3">
        <h3 class="result-title">
          <a href="https://example.com/article" class="result-link">
            Understanding Asyncio Internals
          </a>
        </h3>
        <p class="result-snippet">
          Deep dive into how asyncio event loop works under the hood.
        </p>
      </li>
    </ol>
    <nav class="pagination">
      <a href="/search?q=python+asyncio&page=2" id="next-page" class="btn-next">Next</a>
    </nav>
  </main>
</body>
</html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_complete_search_flow():
    """
    Full AI agent search scenario:
    navigate -> inspect -> type query -> submit -> extract results.
    """
    # Mock server responses
    respx.get("https://websearch.example.com/").mock(
        return_value=httpx.Response(
            200, content=SEARCH_HOME_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    respx.get("https://websearch.example.com/search").mock(
        return_value=httpx.Response(
            200, content=RESULTS_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:

            # - Step 1: Navigate to search home -
            nav = await session.navigate("https://websearch.example.com/")
            assert nav["status"] == "ok"

            # - Step 2: Inspect page -
            page = await session.snapshot()
            assert page.title == "WebSearch"
            assert page.page_type in ("search", "search_results", "form", "generic")

            # Search input should be detected
            assert len(page.inputs) >= 1
            q_input = next(
                (inp for inp in page.inputs
                 if inp.get("attributes", {}).get("name") == "q"),
                None,
            )
            assert q_input is not None

            # - Step 3: Type search query -
            type_result = await session.act({
                "tool": "type",
                "target": "#q",
                "text": "python asyncio",
            })
            assert type_result["status"] == "ok"

            # Verify value set
            q_el = session._current_document.get_element_by_id("q")
            assert q_el.get_attribute("value") == "python asyncio"

            # - Step 4: Click search button -
            click_result = await session.act({
                "tool": "click",
                "target": "#search-btn",
            })
            assert click_result["status"] == "ok"
            assert click_result.get("effects", {}).get("form_submitted") is True

            # - Step 5: Verify navigation to results -
            assert "search" in session.current_url

            # - Step 6: Extract result titles -
            extract_result = await session.act({
                "tool": "extract",
                "query": ".result-title",
            })
            assert extract_result["status"] == "ok"
            assert extract_result["effects"]["count"] == 3

            # - Step 7: Extract result links -
            links_result = await session.act({
                "tool": "extract",
                "query": ".result-link",
            })
            assert links_result["status"] == "ok"
            assert links_result["effects"]["count"] == 3

            link_texts = [r["text"] for r in links_result["effects"]["results"]]
            assert any("asyncio" in t.lower() for t in link_texts)

            # - Step 8: Snapshot of results page -
            results_page = await session.snapshot()
            assert "Results" in results_page.title or "WebSearch" in results_page.title

            # Should see navigation link for "Next"
            links = results_page.semantic_tree.find_by_role("link")
            link_names = [l.name for l in links if l.name]
            assert any("Next" in n for n in link_names)


@pytest.mark.asyncio
@respx.mock
async def test_extract_structured_data():
    """Extract structured data from a results page directly."""
    respx.get("https://websearch.example.com/search").mock(
        return_value=httpx.Response(
            200, content=RESULTS_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://websearch.example.com/search")

            # Extract all result items
            result = await session.act({
                "tool": "extract",
                "query": ".result-item",
            })
            assert result["status"] == "ok"
            assert result["effects"]["count"] == 3

            # Each item should have text content
            for item in result["effects"]["results"]:
                assert item["tag"] == "li"
                assert len(item["text"]) > 0

            # Extract snippets
            snippets = await session.act({
                "tool": "extract",
                "query": ".result-snippet",
            })
            assert snippets["status"] == "ok"
            texts = [r["text"] for r in snippets["effects"]["results"]]
            assert any("asyncio" in t.lower() for t in texts)


@pytest.mark.asyncio
@respx.mock
async def test_click_search_result_link():
    """Navigate to a search result by clicking its link."""
    article_html = b"""
    <html>
    <head><title>Understanding Asyncio Internals</title></head>
    <body>
      <article>
        <h1>Understanding Asyncio Internals</h1>
        <p>The event loop is the core of asyncio...</p>
      </article>
    </body>
    </html>
    """

    respx.get("https://websearch.example.com/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML,
                                    headers={"content-type": "text/html"})
    )
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, content=article_html,
                                    headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://websearch.example.com/search")

            # Find the third result link and click it
            result = await session.act({
                "tool": "click",
                "target": {"by": "text", "text": "Understanding Asyncio"},
            })

            assert result["status"] == "ok"
            assert session.current_url == "https://example.com/article"

            page = await session.snapshot()
            assert "Asyncio" in page.title


@pytest.mark.asyncio
@respx.mock
async def test_pagination_navigation():
    """Navigate to next page of results."""
    page2_html = b"""
    <html>
    <head><title>python asyncio - Page 2 - WebSearch</title></head>
    <body>
      <main>
        <ol class="results-list">
          <li class="result-item">
            <h3><a href="#" class="result-link">More asyncio resources</a></h3>
          </li>
        </ol>
        <nav>
          <a href="/search?q=python+asyncio&page=1" id="prev-page">Previous</a>
          <a href="/search?q=python+asyncio&page=3" id="next-page">Next</a>
        </nav>
      </main>
    </body>
    </html>
    """

    # Register the more-specific (with params) mock first so it takes priority
    respx.get("https://websearch.example.com/search",
              params={"q": "python asyncio", "page": "2"}).mock(
        return_value=httpx.Response(200, content=page2_html,
                                    headers={"content-type": "text/html"})
    )
    respx.get("https://websearch.example.com/search").mock(
        return_value=httpx.Response(200, content=RESULTS_HTML,
                                    headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://websearch.example.com/search")

            # Click "Next" pagination link
            result = await session.act({"tool": "click", "target": "#next-page"})
            assert result["status"] == "ok"
            assert "page=2" in session.current_url

            page = await session.snapshot()
            assert "Page 2" in page.title
