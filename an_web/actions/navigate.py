"""
Navigate action — load a URL and build the DOM.

This is the most fundamental action — all other actions depend on
having a loaded page state.  Mirrors Lightpanda's Navigate.zig:

    1. Policy check
    2. HTTP fetch + redirect follow
    3. HTML parse -> Document tree
    4. Snapshot (URL + DOM hash + storage state)
    5. JS script execution (inline + external <script> tags)
    6. Event loop settle
    7. Return ActionResult with full effects
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

log = logging.getLogger(__name__)


class NavigateAction(Action):
    """
    Load a URL, parse HTML, execute scripts, and settle the page.

    Effects keys:
    - ``navigation``:       True
    - ``final_url``:        URL after all redirects
    - ``status_code``:      HTTP status code
    - ``dom_ready``:        True when DOM is fully built
    - ``redirect_count``:   Number of HTTP redirects followed
    - ``scripts_found``:    Number of inline <script> tags found
    - ``scripts_executed``: Number of scripts sent to JS runtime
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

        # ── 5. Execute inline <script> tags ───────────────────────────
        scripts_found = 0
        scripts_executed = 0
        js_runtime = getattr(session, "js_runtime", None)

        if js_runtime is not None and js_runtime.is_available():
            scripts_found, scripts_executed = await _execute_scripts(
                document, js_runtime
            )

        # ── 6. Settle event loop ──────────────────────────────────────
        if session.scheduler:
            await session.scheduler.drain_microtasks()

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
            },
            state_delta_id=snapshot_id,
            recommended_next_actions=[{"tool": "snapshot"}],
        )


# ─── Script execution helpers ─────────────────────────────────────────────────


async def _execute_scripts(document: Any, js_runtime: Any) -> tuple[int, int]:
    """
    Find all inline <script> tags and evaluate them through the JS runtime.

    Returns ``(found, executed)`` counts.
    External scripts (``<script src="…">``) are counted but not fetched —
    an AI browser doesn't need to execute library code to interact with pages.
    """
    from an_web.dom.nodes import Element

    found = 0
    executed = 0

    for node in document.iter_descendants():
        if not isinstance(node, Element) or node.tag != "script":
            continue

        # Skip non-JS script tags (e.g. type="application/json")
        stype = (node.get_attribute("type") or "").lower()
        if stype and stype not in (
            "text/javascript",
            "application/javascript",
            "module",
            "",
        ):
            continue

        # Skip external scripts (src attribute present)
        if node.get_attribute("src"):
            found += 1
            continue  # external — would need async fetch; skip for now

        # Get inline script content
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

    # Drain any microtasks queued by the scripts
    if found:
        await js_runtime.drain_microtasks()

    return found, executed
