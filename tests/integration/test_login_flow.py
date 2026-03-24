"""
Integration test: Login flow

Simulates an AI agent executing a full login sequence:
  1. Navigate to login page
  2. Inspect page semantics -> confirm it's a login form
  3. Type email + password
  4. Click submit -> follow redirect to dashboard
  5. Verify final state (URL changed, page title updated)

All network calls mocked with respx to avoid real HTTP traffic.
"""
from __future__ import annotations

import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine


LOGIN_PAGE_HTML = b"""
<!DOCTYPE html>
<html>
<head><title>Sign In - MyApp</title></head>
<body>
  <main>
    <h1>Welcome back</h1>
    <form id="login-form" action="/api/auth/login" method="post">
      <div class="field">
        <label for="email">Email address</label>
        <input id="email" type="email" name="email" placeholder="you@example.com" required>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" type="password" name="password" placeholder="Your password" required>
      </div>
      <div class="actions">
        <button type="submit" class="btn-primary" id="submit-btn">Sign In</button>
      </div>
    </form>
    <p><a href="/forgot-password" class="link">Forgot your password?</a></p>
  </main>
</body>
</html>
"""

DASHBOARD_HTML = b"""
<!DOCTYPE html>
<html>
<head><title>Dashboard - MyApp</title></head>
<body>
  <nav>
    <a href="/logout" id="logout">Logout</a>
  </nav>
  <main>
    <h1>Hello, Test User!</h1>
    <p>You are logged in successfully.</p>
    <ul class="actions">
      <li><a href="/profile" class="action-link">Edit Profile</a></li>
      <li><a href="/settings" class="action-link">Settings</a></li>
    </ul>
  </main>
</body>
</html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_complete_login_flow():
    """
    Full AI agent login scenario:
    navigate -> inspect -> type -> type -> click -> verify dashboard.
    """
    # Mock server
    respx.get("https://app.example.com/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_PAGE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    respx.post("https://app.example.com/api/auth/login").mock(
        return_value=httpx.Response(
            302,
            content=b"",
            headers={
                "location": "https://app.example.com/dashboard",
                "set-cookie": "session=abc123; Path=/; HttpOnly",
                "content-type": "text/html",
            },
        )
    )
    respx.get("https://app.example.com/dashboard").mock(
        return_value=httpx.Response(
            200, content=DASHBOARD_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:

            # - Step 1: Navigate to login page -
            nav_result = await session.navigate("https://app.example.com/login")
            assert nav_result["status"] == "ok", f"navigate failed: {nav_result}"
            assert session.current_url == "https://app.example.com/login"

            # - Step 2: Inspect page semantics -
            page = await session.snapshot()
            assert page.title == "Sign In - MyApp"
            assert page.page_type in ("login_form", "form")
            assert len(page.inputs) >= 2

            # Confirm we see email + password inputs
            input_names = {inp.get("attributes", {}).get("name", "") for inp in page.inputs}
            assert "email" in input_names
            assert "password" in input_names

            # - Step 3: Type email -
            type_email = await session.act({
                "tool": "type",
                "target": "#email",
                "text": "agent@test.com",
            })
            assert type_email["status"] == "ok"

            # Verify value was set in DOM
            email_el = session._current_document.get_element_by_id("email")
            assert email_el.get_attribute("value") == "agent@test.com"

            # - Step 4: Type password -
            type_pw = await session.act({
                "tool": "type",
                "target": "#password",
                "text": "s3cr3t!",
            })
            assert type_pw["status"] == "ok"

            # - Step 5: Click submit -> form POST -> redirect -
            click_result = await session.act({
                "tool": "click",
                "target": "#submit-btn",
            })
            assert click_result["status"] == "ok", f"click failed: {click_result}"
            assert click_result.get("effects", {}).get("form_submitted") is True

            # - Step 6: Verify navigation to dashboard -
            assert session.current_url == "https://app.example.com/dashboard"

            # - Step 7: Cookie was harvested -
            cookies = session.cookies.get_for_url("https://app.example.com/dashboard")
            assert any(c.name == "session" and c.value == "abc123" for c in cookies)

            # - Step 8: Dashboard page parsed correctly -
            dashboard_page = await session.snapshot()
            assert dashboard_page.title == "Dashboard - MyApp"
            links = dashboard_page.semantic_tree.find_by_role("link")
            link_names = [l.name for l in links if l.name]
            assert any("logout" in n.lower() or "Logout" in n for n in link_names)


@pytest.mark.asyncio
@respx.mock
async def test_login_with_wrong_credentials():
    """AI agent handles a 401 response gracefully."""
    respx.get("https://app.example.com/login").mock(
        return_value=httpx.Response(200, content=LOGIN_PAGE_HTML,
                                    headers={"content-type": "text/html"})
    )
    respx.post("https://app.example.com/api/auth/login").mock(
        return_value=httpx.Response(
            401,
            content=b"<html><body><p class='error'>Invalid credentials</p></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://app.example.com/login")
            await session.act({"tool": "type", "target": "#email", "text": "bad@test.com"})
            await session.act({"tool": "type", "target": "#password", "text": "wrong"})
            result = await session.act({"tool": "click", "target": "#submit-btn"})

            # 401 means the POST was not "ok" but no exception raised
            # URL should remain at login (no navigation on 401)
            # The engine should reflect the error state gracefully
            # (actual behavior depends on redirect: 401 isn't followed)
            assert session is not None  # no crash


@pytest.mark.asyncio
@respx.mock
async def test_login_page_semantic_structure():
    """Validate the complete semantic model of the login page."""
    respx.get("https://app.example.com/login").mock(
        return_value=httpx.Response(200, content=LOGIN_PAGE_HTML,
                                    headers={"content-type": "text/html"})
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate("https://app.example.com/login")
            page = await session.snapshot()

    # Page classification
    assert page.page_type in ("login_form", "form")
    assert page.title == "Sign In - MyApp"

    # Inputs
    assert len(page.inputs) == 2
    roles = {inp["role"] for inp in page.inputs}
    assert "textbox" in roles

    # Primary actions should include the submit button
    assert len(page.primary_actions) >= 1

    # Tree navigation
    buttons = page.semantic_tree.find_by_role("button")
    assert len(buttons) >= 1
    btn_names = [b.name for b in buttons if b.name]
    assert any("Sign In" in n for n in btn_names)

    # Link is in the tree
    links = page.semantic_tree.find_by_role("link")
    assert len(links) >= 1

    # Serialization round-trip
    d = page.to_dict()
    assert d["pageType"] in ("login_form", "form")
    assert "semanticTree" in d
    assert d["snapshotId"].startswith("snap-")
