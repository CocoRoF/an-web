"""network action — inspect fetch/XHR activity the page performed.

Pages frequently load their real content via fetch/XHR after the initial
HTML.  Even when a framework fails to re-render that data into the DOM,
the network log gives an AI agent direct access to the payloads —
mirroring playwright-mcp's ``browser_network_requests`` tool.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

# Default per-response body preview size (characters)
_DEFAULT_PREVIEW_CHARS = 2_048
# Hard cap for a full body request
_MAX_BODY_CHARS = 200_000


class NetworkAction(Action):
    """
    Report runtime network activity (fetch / XMLHttpRequest).

    Parameters:
    - ``index``:    Return the FULL body of one request by its index.
    - ``max_body``: Preview size per response body (default 2048 chars).
    - ``clear``:    Reset the log after reporting.

    ``effects`` keys:
    - ``count``:    Total logged requests.
    - ``requests``: ``[{index, method, url, status, ok, content_type,
                       elapsed_ms, body_size, body(preview or full)}]``
    """

    async def execute(
        self,
        session: Session,
        index: int | None = None,
        max_body: int = _DEFAULT_PREVIEW_CHARS,
        clear: bool = False,
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        log_list: list[dict[str, Any]] = list(getattr(session, "_network_log", []))

        if index is not None:
            if not 0 <= index < len(log_list):
                return self._make_failure(
                    "network",
                    f"index_out_of_range: {index} (log has {len(log_list)} entries)",
                )
            entries = [(index, log_list[index])]
            body_limit = _MAX_BODY_CHARS
        else:
            entries = list(enumerate(log_list))
            body_limit = max(0, min(max_body, _MAX_BODY_CHARS))

        requests = []
        for i, e in entries:
            body = e.get("body") or ""
            requests.append({
                "index": i,
                "method": e.get("method", "GET"),
                "url": e.get("url", ""),
                "kind": e.get("kind", "fetch"),
                "status": e.get("status", 0),
                "ok": e.get("ok", False),
                "content_type": e.get("content_type", ""),
                "elapsed_ms": e.get("elapsed_ms", 0),
                "body_size": len(body),
                "body": body[:body_limit],
                "truncated": len(body) > body_limit,
                "error": e.get("error"),
            })

        if clear:
            session._network_log = []  # type: ignore[attr-defined]

        return ActionResult(
            status="ok",
            action="network",
            effects={"count": len(log_list), "requests": requests},
        )
