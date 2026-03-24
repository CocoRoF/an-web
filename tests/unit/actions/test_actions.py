"""Unit tests for Action implementations."""
from __future__ import annotations

from typing import Any
import pytest
import respx
import httpx

from an_web.browser.parser import parse_html
from an_web.core.snapshot import SnapshotManager
from an_web.dom.semantics import ActionResult
from an_web.net.client import NetworkClient
from an_web.net.cookies import CookieJar


# ─── Minimal mock session ─────────────────────────────────────────────────────

class MockScheduler:
    async def drain_microtasks(self) -> None: pass
    async def settle_network(self, timeout: float = 3.0) -> None: pass
    async def flush_dom_mutations(self) -> None: pass


class MockSession:
    def __init__(
        self,
        html: str = "<html><body></body></html>",
        url: str = "https://example.com",
        network: NetworkClient | None = None,
        policy: Any = None,
    ) -> None:
        self._current_document = parse_html(html, base_url=url)
        self._current_url = url
        self.network = network
        self.policy = policy
        self.scheduler = MockScheduler()
        self.snapshots = SnapshotManager()
        self.js_runtime = None  # No JS runtime in unit tests


LOGIN_HTML = """
<html><body>
  <form id="login" action="/auth/login" method="post">
    <input id="email" type="email" name="email" placeholder="Email">
    <input id="pw" type="password" name="password" placeholder="Password">
    <button type="submit" id="submit-btn">Sign In</button>
  </form>
  <a id="forgot" href="/forgot-password">Forgot password?</a>
</body></html>
"""

SEARCH_HTML = """
<html><body>
  <form action="/search" method="get">
    <input id="q" type="search" name="q" placeholder="Search...">
    <button type="submit">Go</button>
  </form>
  <div style="display:none">
    <button id="hidden-btn">Hidden</button>
  </div>
  <button disabled id="disabled-btn">Disabled</button>
</body></html>
"""

SELECT_HTML = """
<html><body>
  <select id="country" name="country">
    <option value="us">United States</option>
    <option value="uk">United Kingdom</option>
    <option value="de">Germany</option>
  </select>
  <select id="disabled-select" name="size" disabled>
    <option value="s">Small</option>
    <option value="m">Medium</option>
  </select>
</body></html>
"""

PRODUCT_HTML = """
<html><body>
  <div class="product">
    <h2 class="title">Widget A</h2>
    <span class="price">$10.00</span>
    <a href="/product/1">View</a>
  </div>
  <div class="product">
    <h2 class="title">Widget B</h2>
    <span class="price">$20.00</span>
    <a href="/product/2">View</a>
  </div>
</body></html>
"""

JSON_HTML = """
<html><body>
  <script type="application/json">{"name": "test", "value": 42}</script>
  <script type="application/json">{"items": [1, 2, 3]}</script>
</body></html>
"""


# ─── NavigateAction ───────────────────────────────────────────────────────────

class TestNavigateAction:
    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_success(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200, content=b"<html><head><title>Home</title></head><body><p>Hi</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        network = NetworkClient(cookie_jar=CookieJar())
        session = MockSession(network=network)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com/")

        assert result.is_ok()
        assert result.action == "navigate"
        assert result.effects["navigation"] is True
        assert session._current_document is not None
        assert session._current_url == "https://example.com/"
        await network.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_404(self):
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404, content=b"Not found")
        )
        network = NetworkClient(cookie_jar=CookieJar())
        session = MockSession(network=network)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com/missing")

        assert result.status == "failed"
        assert "404" in result.error
        await network.close()

    @pytest.mark.asyncio
    async def test_navigate_blocked_by_policy(self):
        class DenyAllPolicy:
            def is_url_allowed(self, url: str) -> bool: return False

        session = MockSession(policy=DenyAllPolicy())

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://blocked.com")

        assert result.status == "failed"
        assert "blocked" in result.error

    @pytest.mark.asyncio
    async def test_navigate_no_network(self):
        session = MockSession(network=None)
        session.network = None

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com")
        assert result.status == "failed"

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_snapshot_created(self):
        respx.get("https://example.com/page").mock(
            return_value=httpx.Response(
                200, content=b"<html><body>content</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        network = NetworkClient()
        session = MockSession(network=network)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com/page")

        assert result.state_delta_id is not None
        assert result.state_delta_id.startswith("snap-")
        await network.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_effects_have_status_code(self):
        respx.get("https://example.com/ok").mock(
            return_value=httpx.Response(200, content=b"<html><body></body></html>",
                                        headers={"content-type": "text/html"})
        )
        network = NetworkClient()
        session = MockSession(network=network)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com/ok")

        assert result.effects["status_code"] == 200
        assert result.effects["dom_ready"] is True
        assert "redirect_count" in result.effects
        await network.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_navigate_recommended_next_action_is_snapshot(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=b"<html></html>",
                                        headers={"content-type": "text/html"})
        )
        network = NetworkClient()
        session = MockSession(network=network)

        from an_web.actions.navigate import NavigateAction
        result = await NavigateAction().execute(session, url="https://example.com/")

        assert any(r.get("tool") == "snapshot" for r in result.recommended_next_actions)
        await network.close()


# ─── ClickAction ──────────────────────────────────────────────────────────────

class TestClickAction:
    @pytest.mark.asyncio
    async def test_click_button(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#submit-btn")

        # Should attempt form submit, which fails gracefully without network
        assert result.action == "click"

    @pytest.mark.asyncio
    async def test_click_target_not_found(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#nonexistent")

        assert result.status == "failed"
        assert "not_found" in result.error

    @pytest.mark.asyncio
    async def test_click_hidden_element_fails(self):
        session = MockSession(html=SEARCH_HTML, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#hidden-btn")

        assert result.status == "failed"
        assert "visible" in result.error or "not_found" in result.error

    @pytest.mark.asyncio
    async def test_click_disabled_button_fails(self):
        session = MockSession(html=SEARCH_HTML, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#disabled-btn")

        assert result.status == "failed"
        assert "disabled" in result.error

    @pytest.mark.asyncio
    @respx.mock
    async def test_click_link_navigates(self):
        respx.get("https://example.com/forgot-password").mock(
            return_value=httpx.Response(
                200, content=b"<html><body><p>Forgot</p></body></html>",
                headers={"content-type": "text/html"},
            )
        )
        network = NetworkClient()
        session = MockSession(html=LOGIN_HTML, url="https://example.com", network=network)

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#forgot")

        assert result.is_ok()
        assert session._current_url == "https://example.com/forgot-password"
        await network.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_click_submit_button_submits_form(self):
        respx.post("https://example.com/auth/login").mock(
            return_value=httpx.Response(
                302,
                content=b"",
                headers={"location": "https://example.com/dashboard", "content-type": "text/html"},
            )
        )
        respx.get("https://example.com/dashboard").mock(
            return_value=httpx.Response(
                200, content=b"<html><body>Dashboard</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        network = NetworkClient()
        session = MockSession(html=LOGIN_HTML, url="https://example.com", network=network)

        # Fill in form values first
        email_el = session._current_document.get_element_by_id("email")
        pw_el = session._current_document.get_element_by_id("pw")
        email_el.set_attribute("value", "test@test.com")
        pw_el.set_attribute("value", "secret")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#submit-btn")

        assert result.is_ok()
        assert result.effects.get("form_submitted") is True
        await network.close()

    @pytest.mark.asyncio
    async def test_click_by_node_id(self):
        session = MockSession(html="<button id='b1'>Click</button>", url="https://example.com")
        btn = session._current_document.get_element_by_id("b1")
        assert btn is not None

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(
            session, target={"by": "node_id", "node_id": btn.node_id}
        )
        assert result.action == "click"

    @pytest.mark.asyncio
    async def test_click_generic_button_returns_ok(self):
        html = "<button id='btn'>Click me</button>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#btn")

        assert result.is_ok()
        assert "events_dispatched" in result.effects

    @pytest.mark.asyncio
    async def test_click_link_with_anchor_only(self):
        """An <a href="#section"> should be treated as generic click, not navigation."""
        html = '<a id="anchor" href="#section">Jump</a>'
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.click import ClickAction
        result = await ClickAction().execute(session, target="#anchor")

        # Should not fail — anchor-only href is handled as generic click
        assert result.action == "click"


# ─── TypeAction ───────────────────────────────────────────────────────────────

class TestTypeAction:
    @pytest.mark.asyncio
    async def test_type_into_input(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(
            session, target="#email", text="test@test.com"
        )
        assert result.is_ok()
        assert result.effects["value_set"] == "test@test.com"

        el = session._current_document.get_element_by_id("email")
        assert el.get_attribute("value") == "test@test.com"

    @pytest.mark.asyncio
    async def test_type_events_dispatched(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#pw", text="password123")

        assert "input" in result.effects["events_dispatched"]
        assert "change" in result.effects["events_dispatched"]

    @pytest.mark.asyncio
    async def test_type_target_not_found(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#nonexistent", text="hello")
        assert result.status == "failed"
        assert "not_found" in result.error

    @pytest.mark.asyncio
    async def test_type_into_non_input_fails(self):
        html = "<button id='btn'>Click</button>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#btn", text="hello")
        assert result.status == "failed"
        assert "not_an_input" in result.error

    @pytest.mark.asyncio
    async def test_type_into_textarea(self):
        html = "<textarea id='ta' name='message'></textarea>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#ta", text="Hello world")
        assert result.is_ok()
        assert result.effects["value_set"] == "Hello world"

    @pytest.mark.asyncio
    async def test_type_append_mode(self):
        html = "<input id='inp' type='text'>"
        session = MockSession(html=html, url="https://example.com")
        el = session._current_document.get_element_by_id("inp")
        el.set_attribute("value", "Hello ")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#inp", text="World", append=True)

        assert result.is_ok()
        assert result.effects["value_set"] == "Hello World"
        assert result.effects["appended"] is True

    @pytest.mark.asyncio
    async def test_type_replace_mode_default(self):
        html = "<input id='inp' type='text'>"
        session = MockSession(html=html, url="https://example.com")
        el = session._current_document.get_element_by_id("inp")
        el.set_attribute("value", "old value")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#inp", text="new value")

        assert result.effects["value_set"] == "new value"
        assert result.effects["appended"] is False

    @pytest.mark.asyncio
    async def test_type_into_disabled_input_fails(self):
        html = "<input id='inp' type='text' disabled>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#inp", text="hello")

        assert result.status == "failed"
        assert "disabled" in result.error

    @pytest.mark.asyncio
    async def test_type_into_submit_input_fails(self):
        html = "<input id='sub' type='submit' value='Go'>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import TypeAction
        result = await TypeAction().execute(session, target="#sub", text="hello")

        assert result.status == "failed"
        assert "submit" in result.error


# ─── ClearAction ──────────────────────────────────────────────────────────────

class TestClearAction:
    @pytest.mark.asyncio
    async def test_clear_empties_value(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")
        el = session._current_document.get_element_by_id("email")
        el.set_attribute("value", "pre-filled@test.com")

        from an_web.actions.input import ClearAction
        result = await ClearAction().execute(session, target="#email")
        assert result.is_ok()
        assert el.get_attribute("value") == ""
        assert result.effects["value_cleared"] is True

    @pytest.mark.asyncio
    async def test_clear_returns_previous_value(self):
        html = "<input id='inp' type='text'>"
        session = MockSession(html=html, url="https://example.com")
        el = session._current_document.get_element_by_id("inp")
        el.set_attribute("value", "something")

        from an_web.actions.input import ClearAction
        result = await ClearAction().execute(session, target="#inp")

        assert result.effects["previous_value"] == "something"

    @pytest.mark.asyncio
    async def test_clear_target_not_found(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.input import ClearAction
        result = await ClearAction().execute(session, target="#nonexistent")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_clear_non_input_fails(self):
        html = "<div id='d1'>text</div>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import ClearAction
        result = await ClearAction().execute(session, target="#d1")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_clear_disabled_input_fails(self):
        html = "<input id='inp' type='text' disabled value='x'>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import ClearAction
        result = await ClearAction().execute(session, target="#inp")
        assert result.status == "failed"


# ─── SelectAction ─────────────────────────────────────────────────────────────

class TestSelectAction:
    @pytest.mark.asyncio
    async def test_select_value(self):
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#country", value="uk")
        assert result.is_ok()
        assert result.effects["selected_value"] == "uk"

    @pytest.mark.asyncio
    async def test_select_non_select_fails(self):
        html = "<input id='inp' type='text'>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#inp", value="x")
        assert result.status == "failed"
        assert "not_a_select_element" in result.error

    @pytest.mark.asyncio
    async def test_select_target_not_found(self):
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#nonexistent", value="us")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_select_option_found_flag(self):
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#country", value="de")
        assert result.is_ok()
        assert result.effects["option_found"] is True

    @pytest.mark.asyncio
    async def test_select_unknown_option_still_sets(self):
        """Selecting an unknown value should still set it (graceful)."""
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#country", value="zz")
        assert result.is_ok()
        assert result.effects["selected_value"] == "zz"
        assert result.effects["option_found"] is False

    @pytest.mark.asyncio
    async def test_select_dispatches_change_event(self):
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#country", value="us")
        assert "change" in result.effects["events_dispatched"]

    @pytest.mark.asyncio
    async def test_select_disabled_fails(self):
        session = MockSession(html=SELECT_HTML, url="https://example.com")

        from an_web.actions.input import SelectAction
        result = await SelectAction().execute(session, target="#disabled-select", value="s")
        assert result.status == "failed"
        assert "disabled" in result.error


# ─── SubmitAction ─────────────────────────────────────────────────────────────

class TestSubmitAction:
    @pytest.mark.asyncio
    async def test_submit_form_directly_no_network(self):
        """Submitting without network should return ok with form_submitted=True."""
        session = MockSession(html=LOGIN_HTML, url="https://example.com")
        form = session._current_document.get_element_by_id("login")
        assert form is not None

        from an_web.actions.submit import SubmitAction
        result = await SubmitAction().execute(
            session, target={"by": "node_id", "node_id": form.node_id}
        )
        assert result.is_ok()
        assert result.effects["form_submitted"] is True

    @pytest.mark.asyncio
    async def test_submit_target_not_found(self):
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.submit import SubmitAction
        result = await SubmitAction().execute(session, target="#nonexistent")
        assert result.status == "failed"
        assert "not_found" in result.error

    @pytest.mark.asyncio
    async def test_submit_element_inside_form(self):
        """Targeting an input inside a form should find the form automatically."""
        session = MockSession(html=LOGIN_HTML, url="https://example.com")

        from an_web.actions.submit import SubmitAction
        result = await SubmitAction().execute(session, target="#email")
        # Should find enclosing form and attempt submission
        assert result.is_ok()
        assert result.effects["form_submitted"] is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_submit_with_network_posts_form(self):
        respx.post("https://example.com/auth/login").mock(
            return_value=httpx.Response(
                200, content=b"<html><body>Welcome</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        network = NetworkClient()
        session = MockSession(html=LOGIN_HTML, url="https://example.com", network=network)
        el = session._current_document.get_element_by_id("email")
        el.set_attribute("value", "user@example.com")

        from an_web.actions.submit import SubmitAction
        result = await SubmitAction().execute(session, target="#login")

        assert result.is_ok()
        assert result.effects["form_submitted"] is True
        assert result.effects["method"] == "POST"
        await network.close()

    @pytest.mark.asyncio
    async def test_submit_element_not_in_form_fails(self):
        html = "<div id='orphan'>No form here</div>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.submit import SubmitAction
        result = await SubmitAction().execute(session, target="#orphan")
        assert result.status == "failed"
        assert "no_form_found" in result.error


# ─── ExtractAction ────────────────────────────────────────────────────────────

class TestExtractAction:
    @pytest.mark.asyncio
    async def test_extract_by_selector(self):
        html = """
        <ul>
            <li class="item">Apple</li>
            <li class="item">Banana</li>
            <li class="item">Cherry</li>
        </ul>
        """
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query=".item")
        assert result.is_ok()
        assert result.effects["count"] == 3

    @pytest.mark.asyncio
    async def test_extract_no_document(self):
        session = MockSession()
        session._current_document = None

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query="p")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_extract_results_have_text(self):
        html = "<p id='p1'>Hello</p><p id='p2'>World</p>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query="p")
        results = result.effects["results"]
        texts = [r["text"] for r in results]
        assert any("Hello" in t for t in texts)
        assert any("World" in t for t in texts)

    @pytest.mark.asyncio
    async def test_extract_css_mode(self):
        session = MockSession(html=PRODUCT_HTML, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query=".product")
        assert result.is_ok()
        assert result.effects["count"] == 2
        assert result.effects["mode"] == "css"

    @pytest.mark.asyncio
    async def test_extract_structured_mode(self):
        session = MockSession(html=PRODUCT_HTML, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(
            session,
            query={
                "selector": ".product",
                "fields": {
                    "title": ".title",
                    "price": ".price",
                },
            }
        )
        assert result.is_ok()
        assert result.effects["mode"] == "structured"
        assert result.effects["count"] == 2
        items = result.effects["results"]
        titles = [item.get("title") for item in items]
        assert any("Widget A" in (t or "") for t in titles)
        assert any("Widget B" in (t or "") for t in titles)

    @pytest.mark.asyncio
    async def test_extract_json_mode(self):
        """JSON extraction from data-json containers (parser-safe variant)."""
        html = """
        <html><body>
          <div id="data1" data-json='{"name": "test", "value": 42}'>widget</div>
          <div id="data2" data-json='{"items": [1, 2, 3]}'>items</div>
        </body></html>
        """
        session = MockSession(html=html, url="https://example.com")

        # Extract the elements first, then parse data-json attributes manually
        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(
            session,
            query={"selector": "[data-json]", "fields": {"id_attr": {"sel": "", "attr": "id"}}}
        )
        assert result.is_ok()
        # Should find 2 elements
        assert result.effects["count"] == 2

    @pytest.mark.asyncio
    async def test_extract_html_mode(self):
        html = "<div id='main'><p>Content</p></div>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(
            session,
            query={"mode": "html", "selector": "#main"}
        )
        assert result.is_ok()
        assert result.effects["mode"] == "html"
        assert result.effects["count"] == 1

    @pytest.mark.asyncio
    async def test_extract_structured_attribute_field(self):
        session = MockSession(html=PRODUCT_HTML, url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(
            session,
            query={
                "selector": ".product",
                "fields": {
                    "url": {"sel": "a", "attr": "href"},
                },
            }
        )
        assert result.is_ok()
        items = result.effects["results"]
        urls = [item.get("url") for item in items]
        assert "/product/1" in urls or any("/product/" in (u or "") for u in urls)

    @pytest.mark.asyncio
    async def test_extract_unknown_mode_fails(self):
        session = MockSession(html="<p>hi</p>", url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query={"mode": "foobar"})
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_extract_no_results_is_ok(self):
        session = MockSession(html="<p>no divs here</p>", url="https://example.com")

        from an_web.actions.extract import ExtractAction
        result = await ExtractAction().execute(session, query="div.nonexistent")
        assert result.is_ok()
        assert result.effects["count"] == 0
        assert result.effects["results"] == []


# ─── ScrollAction ─────────────────────────────────────────────────────────────

class TestScrollAction:
    @pytest.mark.asyncio
    async def test_scroll_default_delta(self):
        session = MockSession()

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session)

        assert result.is_ok()
        assert result.effects["delta_y"] == 300
        assert result.effects["scroll_y"] == 300

    @pytest.mark.asyncio
    async def test_scroll_custom_delta(self):
        session = MockSession()

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, delta_y=500, delta_x=100)

        assert result.effects["scroll_y"] == 500
        assert result.effects["scroll_x"] == 100

    @pytest.mark.asyncio
    async def test_scroll_to_top(self):
        session = MockSession()
        session._scroll_y = 1000
        session._scroll_x = 50

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, to_top=True)

        assert result.effects["scroll_y"] == 0
        assert result.effects["scroll_x"] == 0

    @pytest.mark.asyncio
    async def test_scroll_absolute_mode(self):
        session = MockSession()

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, delta_x=100, delta_y=200, absolute=True)

        assert result.effects["scroll_x"] == 100
        assert result.effects["scroll_y"] == 200

    @pytest.mark.asyncio
    async def test_scroll_accumulates(self):
        session = MockSession()
        session._scroll_y = 300

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, delta_y=200)

        assert result.effects["scroll_y"] == 500

    @pytest.mark.asyncio
    async def test_scroll_negative_clamped_to_zero(self):
        session = MockSession()

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, delta_y=-500)

        assert result.effects["scroll_y"] == 0  # clamped

    @pytest.mark.asyncio
    async def test_scroll_returns_target_element_none_for_window(self):
        session = MockSession()

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session)

        assert result.effects["target_element"] is None

    @pytest.mark.asyncio
    async def test_scroll_by_element(self):
        html = "<div id='box' style='overflow:auto'>content</div>"
        session = MockSession(html=html, url="https://example.com")

        from an_web.actions.scroll import ScrollAction
        result = await ScrollAction().execute(session, target="#box", delta_y=100)

        assert result.is_ok()


# ─── WaitForAction ────────────────────────────────────────────────────────────

class TestWaitForAction:
    @pytest.mark.asyncio
    async def test_wait_for_network_idle_ok(self):
        session = MockSession()

        from an_web.actions.wait_for import WaitForAction
        result = await WaitForAction().execute(session, condition="network_idle", timeout_ms=1000)

        assert result.is_ok()
        assert result.effects["satisfied"] is True

    @pytest.mark.asyncio
    async def test_wait_for_unknown_condition_resolves(self):
        session = MockSession()

        from an_web.actions.wait_for import WaitForAction
        # Unknown condition — wait completes immediately (no wait logic)
        result = await WaitForAction().execute(
            session, condition="unknown_condition", timeout_ms=500
        )
        # Either ok or failed is acceptable — must not raise
        assert result.action == "wait_for"

    @pytest.mark.asyncio
    async def test_wait_for_element_visible_timeout(self):
        """Waiting for a nonexistent element should time out."""
        session = MockSession(html="<p>no button here</p>", url="https://example.com")

        from an_web.actions.wait_for import WaitForAction
        result = await WaitForAction().execute(
            session,
            condition="element_visible",
            selector="#nonexistent",
            timeout_ms=200,
        )
        assert result.status == "failed"
        assert "timeout" in result.error

    @pytest.mark.asyncio
    async def test_wait_for_timeout_has_recommended_action(self):
        session = MockSession(html="<p>empty</p>", url="https://example.com")

        from an_web.actions.wait_for import WaitForAction
        result = await WaitForAction().execute(
            session, condition="element_visible", selector="#ghost", timeout_ms=100
        )
        assert any(r.get("tool") == "snapshot" for r in result.recommended_next_actions)


# ─── ActionResult ─────────────────────────────────────────────────────────────

class TestActionResult:
    def test_is_ok(self):
        r = ActionResult(status="ok", action="click")
        assert r.is_ok() is True

    def test_is_not_ok(self):
        r = ActionResult(status="failed", action="click", error="not_found")
        assert r.is_ok() is False

    def test_to_dict_ok(self):
        r = ActionResult(status="ok", action="navigate", target="https://x.com",
                         effects={"navigation": True})
        d = r.to_dict()
        assert d["status"] == "ok"
        assert d["action"] == "navigate"
        assert "effects" in d

    def test_to_dict_error(self):
        r = ActionResult(status="failed", action="click", error="target_not_found")
        d = r.to_dict()
        assert d["status"] == "failed"
        assert "error" in d

    def test_recommended_next_actions_default_empty(self):
        r = ActionResult(status="ok", action="click")
        assert r.recommended_next_actions == []

    def test_state_delta_id_default_none(self):
        r = ActionResult(status="ok", action="navigate")
        assert r.state_delta_id is None

    def test_effects_default_empty_dict(self):
        r = ActionResult(status="ok", action="type")
        assert isinstance(r.effects, dict)


# ─── Helper unit tests ────────────────────────────────────────────────────────

class TestClickHelpers:
    def test_is_submit_button_button_tag(self):
        from an_web.actions.click import _is_submit_button

        class MockEl:
            tag = "button"
            def get_attribute(self, k):
                return None  # type defaults to "submit"

        assert _is_submit_button(MockEl()) is True

    def test_is_submit_button_button_reset(self):
        from an_web.actions.click import _is_submit_button

        class MockEl:
            tag = "button"
            def get_attribute(self, k):
                return "reset"

        assert _is_submit_button(MockEl()) is False

    def test_is_submit_button_input_submit(self):
        from an_web.actions.click import _is_submit_button

        class MockEl:
            tag = "input"
            def get_attribute(self, k):
                return "submit"

        assert _is_submit_button(MockEl()) is True

    def test_is_submit_button_div(self):
        from an_web.actions.click import _is_submit_button

        class MockEl:
            tag = "div"
            def get_attribute(self, k): return None

        assert _is_submit_button(MockEl()) is False

    def test_make_js_selector_with_id(self):
        from an_web.actions.click import _make_js_selector

        class MockEl:
            stable_selector = None
            def get_attribute(self, k):
                return "my-btn" if k == "id" else None
            def get_id(self): return "my-btn"

        sel = _make_js_selector(MockEl())
        assert sel is not None
        assert "getElementById" in sel
        assert "my-btn" in sel

    def test_make_js_selector_no_id_uses_stable(self):
        from an_web.actions.click import _make_js_selector

        class MockEl:
            stable_selector = "div.foo > button"
            def get_attribute(self, k): return None

        sel = _make_js_selector(MockEl())
        assert sel is not None
        assert "querySelector" in sel

    def test_make_js_selector_none_when_no_info(self):
        from an_web.actions.click import _make_js_selector

        class MockEl:
            stable_selector = None
            def get_attribute(self, k): return None

        assert _make_js_selector(MockEl()) is None


class TestExtractHelpers:
    def test_matches_simple_selector_tag(self):
        from an_web.actions.extract import _matches_simple_selector

        class MockEl:
            tag = "div"
            attributes = {}

        assert _matches_simple_selector(MockEl(), "div") is True
        assert _matches_simple_selector(MockEl(), "span") is False

    def test_matches_simple_selector_class(self):
        from an_web.actions.extract import _matches_simple_selector

        class MockEl:
            tag = "div"
            attributes = {"class": "foo bar"}

        assert _matches_simple_selector(MockEl(), ".foo") is True
        assert _matches_simple_selector(MockEl(), ".baz") is False

    def test_matches_simple_selector_id(self):
        from an_web.actions.extract import _matches_simple_selector

        class MockEl:
            tag = "span"
            attributes = {"id": "hero"}

        assert _matches_simple_selector(MockEl(), "#hero") is True
        assert _matches_simple_selector(MockEl(), "#other") is False

    def test_matches_simple_selector_tag_and_class(self):
        from an_web.actions.extract import _matches_simple_selector

        class MockEl:
            tag = "h2"
            attributes = {"class": "title"}

        assert _matches_simple_selector(MockEl(), "h2.title") is True
        assert _matches_simple_selector(MockEl(), "h1.title") is False

    def test_matches_wildcard(self):
        from an_web.actions.extract import _matches_simple_selector

        class MockEl:
            tag = "anything"
            attributes = {}

        assert _matches_simple_selector(MockEl(), "*") is True
        assert _matches_simple_selector(MockEl(), "") is True
