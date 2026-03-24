"""Navigate action — load a URL and build DOM."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class NavigateAction(Action):
    """
    Load a URL, parse HTML, execute scripts, and settle the page.

    This is the most fundamental action — all other actions depend
    on having a loaded page state.
    """

    async def execute(self, session: Session, url: str = "", **kwargs: Any) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        # 1. Policy check
        if session.policy and not session.policy.is_url_allowed(url):
            return self._make_failure(
                "navigate", "url_blocked", target=url,
                recommended=[{"tool": "navigate", "note": "check policy allowlist"}],
            )

        # 2. Fetch document
        if not session.network:
            return self._make_failure("navigate", "network_not_initialized")

        try:
            response = await session.network.get(url)
        except Exception as e:
            return self._make_failure("navigate", f"fetch_error: {e}", target=url)

        if not response.ok:
            return self._make_failure(
                "navigate", f"http_error_{response.status}", target=url
            )

        # 3. Parse HTML → DOM
        from an_web.browser.parser import parse_html
        document = parse_html(response.text, base_url=response.url)
        session._current_document = document  # type: ignore[attr-defined]
        session._current_url = response.url

        # 4. Update state
        if session.snapshots:
            snap = session.snapshots.create(
                url=response.url,
                dom_content=response.text,
                semantic_data={},
            )
            snapshot_id = snap.snapshot_id
        else:
            snapshot_id = ""

        return ActionResult(
            status="ok",
            action="navigate",
            target=url,
            effects={
                "navigation": True,
                "final_url": response.url,
                "status_code": response.status,
                "dom_ready": True,
            },
            state_delta_id=snapshot_id,
        )
