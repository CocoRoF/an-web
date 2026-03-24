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


# ─── SelectAction ─────────────────────────────────────────────────────────────

class TestSelectAction:
    @pytest.mark.asyncio
    async def test_select_value(self):
        html = """
        <select id="country" name="country">
            <option value="us">US</option>
            <option value="uk">UK</option>
        </select>
        """
        session = MockSession(html=html, url="https://example.com")

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
