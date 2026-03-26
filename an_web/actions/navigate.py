"""
Navigate action — load a URL and build the DOM.

This is the most fundamental action — all other actions depend on
having a loaded page state.  Enhanced pipeline:

    1. Policy check
    2. HTTP fetch + redirect follow (with browser-like headers)
    3. HTML parse -> Document tree (preserving script/link tags)
    4. Snapshot (URL + DOM hash + storage state)
    5. Execute scripts: external <script src> fetched and executed,
       inline <script> executed in document order
    6. Event loop settle (microtasks + macrotasks + timers)
    7. Return ActionResult with full effects
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)

# Maximum number of external scripts to fetch per page load
_MAX_EXTERNAL_SCRIPTS = 100
# Maximum total script execution time (seconds)
_MAX_SCRIPT_TIME = 30.0
# Number of event-loop settle rounds after all scripts
_SETTLE_ROUNDS = 10
# Max macrotask wait per settle round (ms)
_MACROTASK_WAIT_MS = 200


class NavigateAction(Action):
    """
    Load a URL, parse HTML, execute scripts, and settle the page.

    Effects keys:
    - ``navigation``:       True
    - ``final_url``:        URL after all redirects
    - ``status_code``:      HTTP status code
    - ``dom_ready``:        True when DOM is fully built
    - ``redirect_count``:   Number of HTTP redirects followed
    - ``scripts_found``:    Number of <script> tags found
    - ``scripts_executed``: Number of scripts successfully executed
    - ``external_loaded``:  Number of external scripts fetched
    """

    async def execute(
        self,
        session: Session,
        url: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        # ── 1. Policy check ───────────────────────────────────────────
        policy_failure = self._check_policy(session, "navigate", url=url)
        if policy_failure is not None:
            return policy_failure

        # ── 2. Fetch document ─────────────────────────────────────────
        if not session.network:
            return self._make_failure("navigate", "network_not_initialized")

        try:
            response = await session.network.get(url)
        except Exception as exc:
            return self._make_failure(
                "navigate", f"fetch_error: {exc}", target=url
            )

        if not response.ok:
            return self._make_failure(
                "navigate", f"http_error_{response.status}", target=url
            )

        # ── 3. Parse HTML -> DOM ──────────────────────────────────────
        from an_web.browser.parser import parse_html

        document = parse_html(response.text, base_url=response.url)
        session._current_document = document  # type: ignore[attr-defined]
        session._current_url = response.url   # type: ignore[attr-defined]

        # ── 4. Snapshot ───────────────────────────────────────────────
        storage_state = getattr(session, "storage_state", lambda: {})()
        snapshot_id = ""
        if session.snapshots:
            snap = session.snapshots.create(
                url=response.url,
                dom_content=response.text,
                semantic_data={},
                storage_state=storage_state,
                network_state={
                    "status": response.status,
                    "redirect_count": response.redirect_count,
                    "elapsed_ms": response.elapsed_ms,
                },
            )
            snapshot_id = snap.snapshot_id

        # ── 5. Execute scripts (inline + external, in document order) ─
        scripts_found = 0
        scripts_executed = 0
        external_loaded = 0
        js_runtime = getattr(session, "js_runtime", None)

        if js_runtime is not None and js_runtime.is_available():
            scripts_found, scripts_executed, external_loaded = (
                await _execute_scripts_full(
                    document, js_runtime, session, response.url
                )
            )

        # ── 5b. Fire DOMContentLoaded + load (HTML5 lifecycle) ────────
        if js_runtime is not None and js_runtime.is_available():
            js_runtime.dispatch_dom_content_loaded()
            await js_runtime.drain_microtasks()
            js_runtime.dispatch_load()
            await js_runtime.drain_microtasks()

        # ── 6. Settle event loop (microtasks + macrotasks) ────────────
        await _settle_page(session, rounds=_SETTLE_ROUNDS)

        # ── 7. Return ActionResult ────────────────────────────────────
        return ActionResult(
            status="ok",
            action="navigate",
            target=url,
            effects={
                "navigation": True,
                "final_url": response.url,
                "status_code": response.status,
                "dom_ready": True,
                "redirect_count": response.redirect_count,
                "scripts_found": scripts_found,
                "scripts_executed": scripts_executed,
                "external_loaded": external_loaded,
            },
            state_delta_id=snapshot_id,
            recommended_next_actions=[{"tool": "snapshot"}],
        )


# ─── Script execution helpers ─────────────────────────────────────────────────


async def _execute_scripts_full(
    document: Any,
    js_runtime: Any,
    session: Any,
    base_url: str,
) -> tuple[int, int, int]:
    """
    Execute all <script> tags respecting the HTML5 script execution model.

    - Inline scripts execute in document order as encountered.
    - External scripts with ``defer`` execute after all inline scripts,
      in document order (matching real browser behavior).
    - External scripts without ``defer`` or ``async`` execute in document
      order at the point they're encountered (blocking).

    Returns ``(found, executed, external_loaded)`` counts.
    """
    from an_web.dom.nodes import Element

    found = 0
    executed = 0
    external_loaded = 0

    # Collect script elements in document order
    script_nodes = []
    for node in document.iter_descendants():
        if isinstance(node, Element) and node.tag == "script":
            script_nodes.append(node)

    # Separate deferred external scripts from immediate-execution scripts
    deferred_scripts: list[Any] = []  # (node, ) pairs for defer="defer" scripts
    immediate_scripts: list[Any] = []

    for node in script_nodes:
        stype = (node.get_attribute("type") or "").lower()
        if stype and stype not in (
            "text/javascript",
            "application/javascript",
            "module",
            "",
        ):
            continue

        src = node.get_attribute("src")
        has_defer = node.get_attribute("defer") is not None

        if src and has_defer:
            deferred_scripts.append(node)
        else:
            immediate_scripts.append(node)

    # Phase 1: Execute inline scripts and non-deferred external scripts
    for node in immediate_scripts:
        src = node.get_attribute("src")
        if src:
            found += 1
            if external_loaded >= _MAX_EXTERNAL_SCRIPTS:
                continue
            resolved_url = urljoin(base_url, src)
            try:
                script_response = await session.network.get(
                    resolved_url,
                    headers={
                        "Sec-Fetch-Dest": "script",
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Site": "same-origin",
                        "Referer": base_url,
                    },
                    resource_type="script",
                )
                if script_response.ok:
                    code = script_response.text
                    if code.strip():
                        result = js_runtime.load_script(
                            code, src_hint=resolved_url
                        )
                        if result.ok:
                            executed += 1
                        external_loaded += 1
                        await js_runtime.drain_microtasks()
            except Exception as exc:
                log.debug("External script fetch failed (%s): %s", resolved_url, exc)
        else:
            code = node.text_content.strip()
            if not code:
                continue
            found += 1
            result = js_runtime.load_script(code, src_hint="<inline-script>")
            if result.ok:
                executed += 1
            else:
                err = result.error
                log.debug("Inline script error: %s", err.message if err else "unknown")
            await js_runtime.drain_microtasks()

    # Phase 2: Execute deferred external scripts (after all inline scripts)
    for node in deferred_scripts:
        src = node.get_attribute("src")
        if not src:
            continue
        found += 1
        if external_loaded >= _MAX_EXTERNAL_SCRIPTS:
            continue
        resolved_url = urljoin(base_url, src)
        try:
            script_response = await session.network.get(
                resolved_url,
                headers={
                    "Sec-Fetch-Dest": "script",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "same-origin",
                    "Referer": base_url,
                },
                resource_type="script",
            )
            if script_response.ok:
                code = script_response.text
                if code.strip():
                    result = js_runtime.load_script(
                        code, src_hint=resolved_url
                    )
                    if result.ok:
                        executed += 1
                    external_loaded += 1
                    await js_runtime.drain_microtasks()
        except Exception as exc:
            log.debug("Deferred script fetch failed (%s): %s", resolved_url, exc)

    return found, executed, external_loaded


async def _settle_page(session: Any, rounds: int = 5) -> None:
    """
    Full page settle: drain microtasks, fire timers, process fetches,
    settle network.

    Runs multiple rounds to handle timer-triggered scripts that enqueue
    more microtasks, timers, or fetch requests.
    """
    js_runtime = getattr(session, "js_runtime", None)
    scheduler = getattr(session, "scheduler", None)

    for _ in range(rounds):
        activity = False

        # 1. Drain microtasks (Promise chains)
        if js_runtime and js_runtime.is_available():
            drained = await js_runtime.drain_microtasks()
            if drained > 0:
                activity = True

        # 2. Run macrotasks (setTimeout callbacks) via scheduler
        if scheduler:
            fired = await scheduler.run_macrotasks(max_wait_ms=_MACROTASK_WAIT_MS)
            if fired > 0:
                activity = True

        # 3. Process pending async fetch requests
        fetched = await _process_pending_fetches(session)
        if fetched > 0:
            activity = True

        # 4. Drain microtasks again (macrotask/fetch callbacks may have queued promises)
        if js_runtime and js_runtime.is_available():
            drained = await js_runtime.drain_microtasks()
            if drained > 0:
                activity = True

        # 5. Network settle
        if scheduler:
            await scheduler.settle_network(timeout=1.0)

        # 6. DOM mutation flush
        if scheduler:
            await scheduler.flush_dom_mutations()

        if not activity:
            break

        # Small yield to let asyncio tasks run
        await asyncio.sleep(0.01)


async def _process_pending_fetches(session: Any) -> int:
    """
    Process any pending async fetch requests queued by JS code.

    Returns the number of fetches processed.
    """
    pending = getattr(session, "_pending_fetches", None)
    if not pending:
        return 0

    network = getattr(session, "network", None)
    if not network:
        return 0

    processed = 0
    # Process all unresolved fetches
    for request_id, info in list(pending.items()):
        if info.get("resolved"):
            continue

        url = info.get("url", "")
        method = info.get("method", "GET")
        headers_json = info.get("headers_json", "null")

        try:
            import json
            headers = json.loads(headers_json) if headers_json and headers_json != "null" else {}

            if "Referer" not in headers:
                headers["Referer"] = getattr(session, "_current_url", "") or ""

            if method.upper() == "GET":
                resp = await network.get(url, headers=headers)
            else:
                resp = await network.get(url, headers=headers)  # simplified

            result = {
                "ok": resp.ok,
                "status": resp.status,
                "text": resp.text,
                "headers": {},
                "url": resp.url,
            }
            info["resolved"] = True
            info["result"] = result
            processed += 1

            # Resolve the JS promise via eval
            js_runtime = getattr(session, "js_runtime", None)
            if js_runtime and js_runtime.is_available():
                # Trigger any waiting code / resolve promises
                await js_runtime.drain_microtasks()

        except Exception as exc:
            log.debug("Async fetch failed for %s: %s", url[:60], exc)
            info["resolved"] = True
            info["result"] = {"ok": False, "status": 0, "text": "", "error": str(exc)}
            processed += 1

    return processed
