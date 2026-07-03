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
# Default wall-clock budget for script execution + settle (seconds).
# Fetch/parse are excluded — they have their own network timeouts.
_NAVIGATE_BUDGET_S = 15.0


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
        timeout: float | None = None,
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        budget_s = timeout if timeout and timeout > 0 else _NAVIGATE_BUDGET_S

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

        # Pre-JS content size — used after settle to detect a client-side
        # render that REPLACED server-rendered content with an empty shell
        # (SPA boot succeeded but its data/render path can't complete in
        # the shim environment). Real browsers never surface that state;
        # for an agent the server-rendered DOM is strictly more truthful.
        _pre_body = document.body
        pre_js_text_len = len(_pre_body.inner_text) if _pre_body is not None else 0

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

        # ── 4b. Re-inject DOM into V8 now that the document is parsed ──
        js_runtime = getattr(session, "js_runtime", None)
        if js_runtime is not None and js_runtime.is_available():
            try:
                from an_web.js.host_api import reinject_dom_state
                reinject_dom_state(js_runtime.ctx, session)
            except Exception:
                pass

        # ── 5. Execute scripts (inline + external, in document order) ─
        scripts_found = 0
        scripts_executed = 0
        external_loaded = 0
        settle_timed_out = False

        # Wall-clock budget for script execution + settle.  Individual V8
        # evals are capped by JSRuntime's per-eval timeout; this asyncio
        # deadline bounds the overall loop between evals.
        try:
            async with asyncio.timeout(budget_s):
                if js_runtime is not None and js_runtime.is_available():
                    # Clear dynamic script queue before initial execution
                    session._pending_dynamic_scripts = []  # type: ignore[attr-defined]
                    scripts_found, scripts_executed, external_loaded = (
                        await _execute_scripts_full(
                            document, js_runtime, session, response.url
                        )
                    )

                # ── 5b. Pre-DCL settle: complete dynamic chunk loads and
                # fetches started during script execution.  Frameworks
                # (Next.js) finalise their bootstrap at DOMContentLoaded;
                # firing it while code-split chunks are still pending
                # truncates their data streams ("Connection closed").
                if js_runtime is not None and js_runtime.is_available():
                    for _ in range(_SETTLE_ROUNDS):
                        activity = await _process_dynamic_scripts(session)
                        activity += await _process_pending_fetches(session)
                        activity += await js_runtime.drain_microtasks()
                        if not activity:
                            break

                # ── 5c. Fire DOMContentLoaded (readyState → interactive) ─
                if js_runtime is not None and js_runtime.is_available():
                    js_runtime.dispatch_dom_content_loaded()
                    await js_runtime.drain_microtasks()

                # ── 6. Settle event loop (micro/macrotasks + dynamic scripts)
                await _settle_page(session, rounds=_SETTLE_ROUNDS)

                # ── 6a. Fire load (readyState → complete), settle handlers
                if js_runtime is not None and js_runtime.is_available():
                    js_runtime.dispatch_load()
                    await js_runtime.drain_microtasks()
                    await _settle_page(session, rounds=3)
        except TimeoutError:
            settle_timed_out = True
            log.warning(
                "navigate settle budget (%.1fs) exceeded for %s — "
                "page returned in current state", budget_s, url,
            )

        # ── 6b. Sync JS DOM mutations back to Python DOM ──────────────
        if js_runtime is not None and js_runtime.is_available():
            try:
                from an_web.js.host_api import sync_dom_mutations
                sync_dom_mutations(js_runtime.ctx, session)
            except Exception:
                pass

        # ── 6c. SSR-preservation fallback ─────────────────────────────
        # If page JS shrank the visible text to under 30% of the parsed
        # HTML (e.g. an app shell replacing full server-rendered content),
        # restore the pre-JS DOM. Flagged in effects as dom_restored.
        dom_restored = False
        if scripts_executed > 0 and pre_js_text_len > 1000:
            try:
                cur_doc = getattr(session, "_current_document", document)
                cur_body = cur_doc.body if cur_doc is not None else None
                post_text_len = len(cur_body.inner_text) if cur_body is not None else 0
                if post_text_len < pre_js_text_len * 0.3:
                    session._current_document = parse_html(  # type: ignore[attr-defined]
                        response.text, base_url=response.url
                    )
                    dom_restored = True
                    log.warning(
                        "navigate: page JS reduced visible text %d -> %d chars on %s "
                        "— restored pre-JS DOM (client render incomplete in shim env)",
                        pre_js_text_len, post_text_len, response.url,
                    )
            except Exception:
                pass

        # Count dynamic scripts that were loaded during settle
        dynamic_loaded = len(js_runtime._scripts_loaded) - scripts_found \
            if js_runtime and js_runtime.is_available() else 0
        dynamic_loaded = max(0, dynamic_loaded)

        # ── 7. Return ActionResult ────────────────────────────────────
        total_scripts = scripts_found + dynamic_loaded
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
                "scripts_found": total_scripts,
                "scripts_executed": scripts_executed,
                "external_loaded": external_loaded,
                "dynamic_loaded": dynamic_loaded,
                "settle_timeout": settle_timed_out,
                "dom_restored": dom_restored,
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
    load dynamic scripts, settle network.

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

        # 4. Load dynamically inserted <script> elements
        loaded = await _process_dynamic_scripts(session)
        if loaded > 0:
            activity = True

        # 5. Drain microtasks again (macrotask/fetch callbacks may have queued promises)
        if js_runtime and js_runtime.is_available():
            drained = await js_runtime.drain_microtasks()
            if drained > 0:
                activity = True

        # 6. Network settle
        if scheduler:
            await scheduler.settle_network(timeout=1.0)

        # 7. DOM mutation flush
        if scheduler:
            await scheduler.flush_dom_mutations()

        if not activity:
            break

        # Small yield to let asyncio tasks run
        await asyncio.sleep(0.01)


async def _process_pending_fetches(session: Any) -> int:
    """
    Perform HTTP requests queued by page JS (fetch / XMLHttpRequest) and
    resolve the corresponding pending Promises / XHR events in V8 via
    ``_resolveFetch(id, dataJson)``.

    Every request/response pair is also appended to
    ``session._network_log`` so AI agents can inspect what data the page
    loaded, even when the framework fails to re-render it into the DOM.

    Returns the number of fetches processed.
    """
    pending = getattr(session, "_pending_fetches", None)
    if not pending:
        return 0

    network = getattr(session, "network", None)
    if not network:
        return 0

    import json as _json
    import time as _time

    js_runtime = getattr(session, "js_runtime", None)
    base_url = getattr(session, "_current_url", "") or ""
    processed = 0

    for request_id, info in list(pending.items()):
        if info.get("resolved"):
            pending.pop(request_id, None)
            continue
        info["resolved"] = True

        raw_url = info.get("url", "")
        url = urljoin(base_url, raw_url) if base_url else raw_url
        method = (info.get("method") or "GET").upper()
        headers_json = info.get("headers_json", "null")
        try:
            headers = (
                _json.loads(headers_json)
                if headers_json and headers_json != "null" else {}
            )
            if not isinstance(headers, dict):
                headers = {}
        except Exception:
            headers = {}
        headers.setdefault("Referer", base_url)

        body = info.get("body")
        body_bytes = body.encode("utf-8") if isinstance(body, str) else None

        t0 = _time.monotonic()
        try:
            resp = await network.fetch(
                url, method=method, headers=headers, body=body_bytes,
                resource_type="xhr" if info.get("kind") == "xhr" else "fetch",
            )
            result = {
                "ok": resp.ok,
                "status": resp.status,
                "statusText": "",
                "text": resp.text,
                "headers": dict(getattr(resp, "headers", None) or {}),
                "url": resp.url,
                "redirected": getattr(resp, "redirect_count", 0) > 0,
            }
        except Exception as exc:
            log.debug("Async fetch failed for %s: %s", url[:80], exc)
            result = {
                "ok": False, "status": 0, "text": "",
                "error": str(exc)[:300], "headers": {}, "url": url,
            }
        elapsed_ms = round((_time.monotonic() - t0) * 1000, 1)

        _record_network_activity(session, {
            "url": url,
            "method": method,
            "kind": info.get("kind", "fetch"),
            "status": result.get("status", 0),
            "ok": result.get("ok", False),
            "content_type": (result.get("headers") or {}).get("content-type", ""),
            "body": result.get("text", ""),
            "error": result.get("error"),
            "elapsed_ms": elapsed_ms,
        })

        if js_runtime is not None and js_runtime.is_available():
            payload = _json.dumps(_json.dumps(result))
            js_runtime.eval_safe(
                "typeof _resolveFetch === 'function' "
                f"? _resolveFetch({_json.dumps(request_id)}, {payload}) : false"
            )
            await js_runtime.drain_microtasks()

        pending.pop(request_id, None)
        processed += 1

    return processed


def _record_network_activity(session: Any, entry: dict[str, Any]) -> None:
    """Append a request/response record to the session network log."""
    log_list = getattr(session, "_network_log", None)
    if log_list is None:
        session._network_log = []
        log_list = session._network_log
    # Bound memory: keep the newest 200 entries
    if len(log_list) >= 200:
        del log_list[: len(log_list) - 199]
    log_list.append(entry)


async def _process_dynamic_scripts(session: Any) -> int:
    """
    Fetch and execute dynamically inserted <script src> elements.

    When JS code does ``document.createElement('script')`` +
    ``el.src = '...'`` + ``parent.appendChild(el)``, the script URL
    is queued in ``session._pending_dynamic_scripts``.  This function
    fetches and evaluates those scripts, mirroring real browser behaviour.

    Returns the number of scripts loaded.
    """
    pending = getattr(session, "_pending_dynamic_scripts", None)
    if not pending:
        return 0

    network = getattr(session, "network", None)
    js_runtime = getattr(session, "js_runtime", None)
    if not network or not js_runtime or not js_runtime.is_available():
        return 0

    import json as _json

    base_url = getattr(session, "_current_url", "about:blank") or "about:blank"
    loaded = 0
    # Drain the queue (scripts may enqueue more during execution)
    while pending:
        entry = pending.pop(0)
        src = entry["src"]
        node_id = entry.get("node_id")
        resolved_url = urljoin(base_url, src)
        ok = False
        try:
            resp = await network.get(
                resolved_url,
                headers={
                    "Sec-Fetch-Dest": "script",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "same-origin",
                    "Referer": base_url,
                },
                resource_type="script",
            )
            if resp.ok and resp.text.strip():
                result = js_runtime.load_script(resp.text, src_hint=resolved_url)
                ok = result.ok
                await js_runtime.drain_microtasks()
                loaded += 1
        except Exception as exc:
            log.debug("Dynamic script load failed (%s): %s", resolved_url[:60], exc)

        # Fire the script element's load/error event — webpack's chunk
        # loader (__webpack_require__.l) resolves its chunk Promise from
        # exactly this event; without it every code-split import hangs.
        if node_id:
            event = "load" if ok else "error"
            js_runtime.eval_safe(
                f"(function() {{"
                f"  var el = _elementCache[{_json.dumps(node_id)}];"
                f"  if (!el) return false;"
                f"  var ev = {{type: {_json.dumps(event)}, target: el}};"
                f"  if (el.on{event}) try {{ el.on{event}(ev); }} catch(e) {{}}"
                f"  if (el.dispatchEvent) try {{ el.dispatchEvent(ev); }} catch(e) {{}}"
                f"  return true;"
                f"}})()"
            )
            await js_runtime.drain_microtasks()

    return loaded
