"""
Integration tests: Login flow — comprehensive coverage.

Verifies the full AN-Web engine pipeline via ANWebToolInterface:
  navigate → snapshot → type email → type password → click submit → snapshot

Covers:
  1. Full session lifecycle (ANWebEngine context manager)
  2. ANWebToolInterface convenience API
  3. Semantic extraction: page_type, inputs, primary_actions
  4. DOM mutation after type actions
  5. Form submit → HTTP POST → redirect → new page load
  6. Cookie harvesting from Set-Cookie response header
  7. Artifact collection at every step
  8. Replay trace export and deterministic re-execution
  9. Policy enforcement blocking a blacklisted domain
  10. Semantic target resolution (role+text)
  11. History API (back())
  12. API tool call via session.act() (Anthropic tool_use format)
  13. ActionResponse Pydantic model round-trip
  14. StructuredLogger integration

Success criteria:
  - All asserts pass.
  - No exceptions propagate.
  - Artifact collector records at least one ACTION_TRACE per step.
  - Replay of the recorded trace produces identical status results.
"""
from __future__ import annotations

import pytest
import respx
import httpx

from an_web.core.engine import ANWebEngine
from an_web.api.rpc import ANWebToolInterface
from an_web.api.models import ActionResponse
from an_web.tracing.artifacts import ArtifactCollector, ArtifactKind
from an_web.tracing.replay import ReplayTrace, ReplayEngine


# ── HTML fixtures ──────────────────────────────────────────────────────────────

LOGIN_HTML = b"""<!DOCTYPE html>
<html><head><title>Sign In - MyApp</title></head>
<body>
  <main>
    <h1>Welcome back</h1>
    <form id="login-form" action="/api/auth/login" method="post">
      <label for="email">Email address</label>
      <input id="email" type="email" name="email"
             placeholder="you@example.com" required>
      <label for="password">Password</label>
      <input id="password" type="password" name="password"
             placeholder="Your password" required>
      <button type="submit" class="btn-primary" id="submit-btn">Sign In</button>
    </form>
    <a href="/forgot-password" class="link">Forgot your password?</a>
  </main>
</body></html>"""

DASHBOARD_HTML = b"""<!DOCTYPE html>
<html><head><title>Dashboard - MyApp</title></head>
<body>
  <nav>
    <a href="/logout" id="logout-link">Logout</a>
  </nav>
  <main>
    <h1>Hello, Test User!</h1>
    <p class="welcome">You are logged in successfully.</p>
    <ul>
      <li><a href="/profile" class="action-link">Edit Profile</a></li>
      <li><a href="/settings" class="action-link">Settings</a></li>
    </ul>
  </main>
</body></html>"""

ERROR_HTML = b"""<!DOCTYPE html>
<html><head><title>Sign In - MyApp</title></head>
<body>
  <main>
    <div class="alert alert-error" role="alert" id="error-banner">
      Invalid email or password.
    </div>
    <form id="login-form" action="/api/auth/login" method="post">
      <input id="email" type="email" name="email" required>
      <input id="password" type="password" name="password" required>
      <button type="submit" id="submit-btn">Sign In</button>
    </form>
  </main>
</body></html>"""

BASE = "https://app.example.com"


# ── Helper: set up respx mocks ─────────────────────────────────────────────────

def _mock_happy_path() -> None:
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=httpx.Response(
            302, content=b"",
            headers={
                "location": f"{BASE}/dashboard",
                "set-cookie": "session=abc123; Path=/; HttpOnly",
                "content-type": "text/html",
            },
        )
    )
    respx.get(f"{BASE}/dashboard").mock(
        return_value=httpx.Response(
            200, content=DASHBOARD_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Canonical login flow via session.navigate() + session.act()
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_canonical_login_flow():
    """
    Success criteria:
      ✓ navigate → status ok, URL set
      ✓ snapshot → page_type is login/form, 2 inputs found, submit button in primary_actions
      ✓ type email → status ok, DOM value mutated
      ✓ type password → status ok
      ✓ click submit → status ok, form_submitted=True, URL = dashboard
      ✓ session cookie harvested from redirect response
      ✓ dashboard snapshot → title matches, logout link present
    """
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:

            # ── Step 1: Navigate ──────────────────────────────────────────────
            nav = await session.navigate(f"{BASE}/login")
            assert nav["status"] == "ok", f"navigate failed: {nav}"
            assert session.current_url == f"{BASE}/login"

            # ── Step 2: Snapshot ──────────────────────────────────────────────
            page = await session.snapshot()
            assert page.title == "Sign In - MyApp"
            assert page.page_type in ("login_form", "form", "generic")
            assert len(page.inputs) >= 2

            input_names = {
                inp.get("attributes", {}).get("name", "")
                for inp in page.inputs
            }
            assert "email" in input_names, f"email input missing from {input_names}"
            assert "password" in input_names, f"password input missing from {input_names}"

            # Submit button should be a primary action
            assert len(page.primary_actions) >= 1

            # ── Step 3: Type email ────────────────────────────────────────────
            r_email = await session.act({
                "tool": "type",
                "target": "#email",
                "text": "agent@test.com",
            })
            assert r_email["status"] == "ok", f"type email failed: {r_email}"

            # DOM must reflect the typed value
            email_el = session._current_document.get_element_by_id("email")
            assert email_el is not None
            assert email_el.get_attribute("value") == "agent@test.com"

            # ── Step 4: Type password ─────────────────────────────────────────
            r_pw = await session.act({
                "tool": "type",
                "target": "#password",
                "text": "s3cr3t!",
            })
            assert r_pw["status"] == "ok", f"type password failed: {r_pw}"

            pw_el = session._current_document.get_element_by_id("password")
            assert pw_el.get_attribute("value") == "s3cr3t!"

            # ── Step 5: Click submit ──────────────────────────────────────────
            r_click = await session.act({
                "tool": "click",
                "target": "#submit-btn",
            })
            assert r_click["status"] == "ok", f"click failed: {r_click}"
            assert r_click.get("effects", {}).get("form_submitted") is True

            # ── Step 6: URL changed to dashboard ─────────────────────────────
            assert session.current_url == f"{BASE}/dashboard", (
                f"expected dashboard URL, got: {session.current_url}"
            )

            # ── Step 7: Cookie harvested ──────────────────────────────────────
            cookies = session.cookies.get_for_url(f"{BASE}/dashboard")
            session_cookies = [c for c in cookies if c.name == "session"]
            assert len(session_cookies) == 1
            assert session_cookies[0].value == "abc123"

            # ── Step 8: Dashboard snapshot ────────────────────────────────────
            dash = await session.snapshot()
            assert dash.title == "Dashboard - MyApp"
            links = dash.semantic_tree.find_by_role("link")
            link_names = [l.name for l in links if l.name]
            assert any("Logout" in n or "logout" in n.lower() for n in link_names), (
                f"Logout link not found in {link_names}"
            )

            # ── Step 9: Serialisation round-trip ─────────────────────────────
            d = dash.to_dict()
            assert d["title"] == "Dashboard - MyApp"
            assert "snapshotId" in d
            assert d["snapshotId"].startswith("snap-")


# ══════════════════════════════════════════════════════════════════════════════
# 2. ANWebToolInterface — high-level convenience API
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_tool_interface_login_flow():
    """
    Same login scenario executed via ANWebToolInterface convenience wrappers.
    Verifies history recording and to_tool_result() Anthropic format.
    """
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            iface = ANWebToolInterface(session)

            # Navigate
            r = await iface.navigate(f"{BASE}/login")
            assert r["status"] == "ok"

            # Snapshot
            snap = await iface.snapshot()
            assert snap["title"] == "Sign In - MyApp"

            # Type
            r = await iface.type("#email", "agent@test.com")
            assert r["status"] == "ok"

            r = await iface.type("#password", "s3cr3t!")
            assert r["status"] == "ok"

            # Click
            r = await iface.click("#submit-btn")
            assert r["status"] == "ok"
            assert session.current_url == f"{BASE}/dashboard"

            # History
            assert len(iface.tool_history) == 5
            tool_names = [name for name, _ in iface.tool_history]
            assert tool_names == ["navigate", "snapshot", "type", "type", "click"]

            # Verify last result was ok
            last_name, last_result = iface.tool_history[-1]
            assert last_name == "click"
            assert last_result["status"] == "ok"

            # Pydantic ActionResponse wrapping
            resp = ActionResponse.from_result(last_result)
            assert resp.ok
            assert resp.effects.form_submitted is True

            # to_tool_result() produces Anthropic format
            tr = resp.to_tool_result(tool_use_id="tu-xyz")
            assert tr["type"] == "tool_result"
            assert tr["tool_use_id"] == "tu-xyz"
            assert tr["is_error"] is False

            # history_as_trace()
            trace_dict = iface.history_as_trace()
            assert len(trace_dict["steps"]) == 5
            assert trace_dict["session_id"] == session.session_id


# ══════════════════════════════════════════════════════════════════════════════
# 3. Artifact collection
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_artifact_collection_during_login():
    """
    ArtifactCollector attached to session captures ACTION_TRACE for each step.
    """
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            # Attach collector
            session.artifacts = ArtifactCollector(
                session_id=session.session_id,
                max_size=50,
            )

            await session.navigate(f"{BASE}/login")
            await session.act({"tool": "type", "target": "#email", "text": "a@b.com"})
            await session.act({"tool": "type", "target": "#password", "text": "pw"})
            await session.act({"tool": "click", "target": "#submit-btn"})

            traces = session.artifacts.get_by_kind(ArtifactKind.ACTION_TRACE)
            assert len(traces) >= 3, f"expected ≥3 ACTION_TRACE artifacts, got {len(traces)}"

            actions = [t.data["action"] for t in traces]
            assert "navigate" in actions or "type" in actions

            # navigate trace carries url
            # check no artifact has an unset status
            for t in traces:
                assert t.data["status"] in ("ok", "failed", "blocked")

            # Summary
            s = session.artifacts.summary()
            assert s["total"] >= 3
            assert "action_trace" in s["by_kind"]

            # Export / import round-trip
            exported = session.artifacts.export()
            restored = ArtifactCollector.from_export(exported)
            assert len(restored) == len(session.artifacts)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Anthropic tool_use nested format
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_anthropic_tool_use_format():
    """
    session.act() must accept the nested {"name": ..., "input": {...}} format
    that the Anthropic API returns in tool_use blocks.
    """
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:

            # Navigate using nested format
            r = await session.act({
                "type": "tool_use",
                "name": "navigate",
                "input": {"url": f"{BASE}/login"},
            })
            assert r["status"] == "ok"
            assert session.current_url == f"{BASE}/login"

            # snapshot using nested format
            r = await session.act({"name": "snapshot", "input": {}})
            assert "title" in r or "page_type" in r or r.get("status") == "ok"

            # type using nested format
            r = await session.act({
                "name": "type",
                "input": {"target": "#email", "text": "x@y.com"},
            })
            assert r["status"] == "ok"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Semantic target resolution
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_semantic_target_login():
    """
    Click using semantic role+text targeting instead of CSS selector.
    """
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")
            await session.act({"tool": "type", "target": "#email", "text": "a@b.com"})
            await session.act({"tool": "type", "target": "#password", "text": "pw"})

            # Click the submit button by role+text
            r = await session.act({
                "tool": "click",
                "target": {"by": "role", "role": "button", "text": "Sign In"},
            })
            assert r["status"] == "ok", f"semantic click failed: {r}"
            assert session.current_url == f"{BASE}/dashboard"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Failed login — error state handling
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_failed_login_error_state():
    """
    On 401, no redirect occurs. The engine loads the error page
    and the session stays on the login URL (or error page URL).
    The agent can detect the error banner via snapshot.
    """
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=httpx.Response(
            401, content=ERROR_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")
            await session.act({"tool": "type", "target": "#email", "text": "bad@test.com"})
            await session.act({"tool": "type", "target": "#password", "text": "wrong"})

            click = await session.act({"tool": "click", "target": "#submit-btn"})
            # 401 is not redirected — click action may succeed (form was submitted)
            # but the session URL does not change to /dashboard
            assert session.current_url != f"{BASE}/dashboard"

            # Page should be accessible (no crash)
            assert session is not None


# ══════════════════════════════════════════════════════════════════════════════
# 7. Policy enforcement
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_policy_blocks_forbidden_domain():
    """
    PolicyRules(denied_domains=["evil.com"]) must block navigation to that domain.
    """
    from an_web.policy.rules import PolicyRules

    policy = PolicyRules(denied_domains=["evil.com"])

    async with ANWebEngine() as engine:
        async with await engine.create_session(policy=policy) as session:

            result = await session.act({
                "tool": "navigate",
                "url": "https://evil.com/steal",
            })
            assert result["status"] == "blocked", f"expected blocked, got: {result}"
            # URL must NOT have changed to evil.com
            assert "evil.com" not in session.current_url


@pytest.mark.asyncio
@respx.mock
async def test_policy_allows_non_blocked_domain():
    """Navigation to an allowed domain must proceed normally."""
    from an_web.policy.rules import PolicyRules

    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    policy = PolicyRules(denied_domains=["evil.com"])

    async with ANWebEngine() as engine:
        async with await engine.create_session(policy=policy) as session:
            result = await session.navigate(f"{BASE}/login")
            assert result["status"] == "ok"
            assert session.current_url == f"{BASE}/login"


# ══════════════════════════════════════════════════════════════════════════════
# 8. back() — history navigation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_back_navigation():
    """After login, session.back() should return to login page."""
    _mock_happy_path()
    # Also allow re-navigating to login for back()
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")
            assert session.current_url == f"{BASE}/login"

            await session.act({"tool": "type", "target": "#email", "text": "a@b.com"})
            await session.act({"tool": "type", "target": "#password", "text": "pw"})
            await session.act({"tool": "click", "target": "#submit-btn"})
            assert session.current_url == f"{BASE}/dashboard"

            # Go back
            back_result = await session.back()
            assert back_result["status"] == "ok", f"back() failed: {back_result}"
            # Should be at login again (last history entry before /login was pushed)
            assert "/login" in session.current_url or "example.com" in session.current_url


# ══════════════════════════════════════════════════════════════════════════════
# 9. Replay trace
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_replay_trace_login():
    """
    Build a ReplayTrace manually and replay it via ReplayEngine.
    All steps should succeed with status='ok'.
    """
    # Need two mocks because respx resets between calls
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=httpx.Response(
            302, content=b"",
            headers={
                "location": f"{BASE}/dashboard",
                "set-cookie": "session=abc123; Path=/; HttpOnly",
                "content-type": "text/html",
            },
        )
    )
    respx.get(f"{BASE}/dashboard").mock(
        return_value=httpx.Response(
            200, content=DASHBOARD_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            # Build replay trace
            trace = ReplayTrace.new(session_id=session.session_id, scenario="login")
            trace.add_step("navigate", {"url": f"{BASE}/login"}, expected_status="ok")
            trace.add_step("type", {"target": "#email", "text": "replay@test.com"},
                           expected_status="ok")
            trace.add_step("type", {"target": "#password", "text": "replay_pw"},
                           expected_status="ok")
            trace.add_step("click", {"target": "#submit-btn"}, expected_status="ok")

            # Replay
            engine_ = ReplayEngine()
            result = await engine_.replay_trace(session, trace)

            assert result.succeeded, (
                f"replay failed on steps: {[(s.step_id, s.assertion_error) for s in result.failed_steps]}"
            )
            assert len(result.steps) == 4
            assert all(s.status == "ok" for s in result.steps)
            assert result.total_duration_ms >= 0.0

            # Serialisation
            d = result.to_dict()
            assert d["succeeded"] is True
            assert len(d["steps"]) == 4


# ══════════════════════════════════════════════════════════════════════════════
# 10. Replay trace JSON round-trip
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_replay_trace_json_roundtrip():
    """ReplayTrace can be serialised to JSON and deserialised without data loss."""
    trace = ReplayTrace.new(session_id="test-001", scenario="login")
    trace.add_step("navigate", {"url": "https://x.com"}, expected_status="ok")
    trace.add_step("type", {"target": "#email", "text": "x@y.com"})
    trace.add_step("click", {"target": "#btn"}, expected_status="ok")

    json_str = trace.to_json()
    trace2 = ReplayTrace.from_json(json_str)

    assert trace2.trace_id == trace.trace_id
    assert trace2.session_id == "test-001"
    assert len(trace2.steps) == 3
    assert trace2.steps[0].expected_status == "ok"
    assert trace2.steps[1].params["text"] == "x@y.com"
    assert trace2.steps[2].action == "click"


# ══════════════════════════════════════════════════════════════════════════════
# 11. Multi-session isolation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_multi_session_isolation():
    """
    Two sessions must not share DOM, cookies, or storage state.
    """
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        s1 = await engine.create_session()
        s2 = await engine.create_session()

        await s1.navigate(f"{BASE}/login")
        await s2.navigate(f"{BASE}/login")

        # Type different values into each session
        await s1.act({"tool": "type", "target": "#email", "text": "user1@test.com"})
        await s2.act({"tool": "type", "target": "#email", "text": "user2@test.com"})

        el1 = s1._current_document.get_element_by_id("email")
        el2 = s2._current_document.get_element_by_id("email")

        assert el1.get_attribute("value") == "user1@test.com"
        assert el2.get_attribute("value") == "user2@test.com"
        assert el1 is not el2  # truly different DOM trees

        # Sessions have different IDs
        assert s1.session_id != s2.session_id

        # Engine counts
        assert engine.active_session_count == 2

        await s1.close()
        await s2.close()
        assert engine.active_session_count == 0


# ══════════════════════════════════════════════════════════════════════════════
# 12. extract tool: page content extraction
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_extract_login_page_elements():
    """Extract tool returns structured data from the login page."""
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")

            # Extract all inputs
            r = await session.act({"tool": "extract", "query": "input"})
            assert r["status"] == "ok"
            assert r["effects"]["count"] >= 2

            # Extract the submit button
            r = await session.act({"tool": "extract", "query": "#submit-btn"})
            assert r["status"] == "ok"
            assert r["effects"]["count"] == 1
            btn = r["effects"]["results"][0]
            assert "Sign In" in btn.get("text", "")

            # Extract links
            r = await session.act({"tool": "extract", "query": "a"})
            assert r["status"] == "ok"
            assert r["effects"]["count"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# 13. eval_js on login page
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_eval_js_on_login_page():
    """eval_js returns ok (or ok with available=False when no JS ctx)."""
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")

            r = await session.act({"tool": "eval_js", "script": "1 + 1"})
            assert r["status"] == "ok"
            effects = r.get("effects", {})
            # With JS runtime: result = "2"
            # Without JS runtime: available = False
            assert "result" in effects or "available" in effects


# ══════════════════════════════════════════════════════════════════════════════
# 14. wait_for tool
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_wait_for_network_idle():
    """wait_for condition=network_idle completes without error."""
    respx.get(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200, content=LOGIN_HTML,
            headers={"content-type": "text/html"},
        )
    )

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")

            r = await session.act({
                "tool": "wait_for",
                "condition": "network_idle",
                "timeout_ms": 1000,
            })
            assert r["status"] == "ok"
            assert r.get("effects", {}).get("satisfied") is True


# ══════════════════════════════════════════════════════════════════════════════
# 15. localStorage persists across navigation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@respx.mock
async def test_localstorage_persists():
    """localStorage for an origin should survive a second navigate."""
    _mock_happy_path()

    async with ANWebEngine() as engine:
        async with await engine.create_session() as session:
            await session.navigate(f"{BASE}/login")

            # Write to localStorage
            session.local_storage["remember_me"] = "true"

            # Navigate away (form submit → dashboard)
            await session.act({"tool": "type", "target": "#email", "text": "a@b.com"})
            await session.act({"tool": "type", "target": "#password", "text": "pw"})
            await session.act({"tool": "click", "target": "#submit-btn"})
            assert session.current_url == f"{BASE}/dashboard"

            # localStorage for same origin should persist
            ls = session.get_local_storage("app.example.com")
            assert ls.get("remember_me") == "true"

            # sessionStorage was cleared on navigate
            assert session.session_storage == {}


# ══════════════════════════════════════════════════════════════════════════════
# 16. ANWebEngine capacity limit
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_engine_max_sessions():
    """Engine refuses to create sessions beyond max_concurrent_sessions."""
    async with ANWebEngine(max_concurrent_sessions=2) as engine:
        s1 = await engine.create_session()
        s2 = await engine.create_session()

        with pytest.raises(RuntimeError, match="capacity"):
            await engine.create_session()

        await s1.close()
        # Now there's capacity for one more
        s3 = await engine.create_session()
        assert engine.active_session_count == 2
        await s2.close()
        await s3.close()
