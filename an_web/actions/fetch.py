"""fetch action — agent-initiated HTTP request in the session context.

The equivalent of Playwright's APIRequestContext: performs a request with
the session's cookies and policy rules, without going through the page's
V8 world.  This is the reliable way for an AI agent to pull data from the
APIs a page uses — hostile or broken page JS cannot interfere.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult

# Response bodies larger than this are truncated in the result
_MAX_BODY_CHARS = 200_000


class FetchAction(Action):
    """
    Perform an HTTP request with the session's cookies and policy.

    Parameters:
    - ``url``:     Absolute URL, or relative to the current page.
    - ``method``:  HTTP method (default GET).
    - ``headers``: Extra request headers.
    - ``body``:    Request body string (for POST/PUT/PATCH).
    - ``max_body``: Response body cap in characters (default 200k).

    ``effects`` keys:
    - ``status``, ``ok``, ``url`` (final, after redirects)
    - ``content_type``
    - ``body``: response text (JSON responses also parsed into ``json``)
    - ``json``: parsed body when the response is JSON, else None
    - ``truncated``: True if body was cut at max_body
    """

    async def execute(
        self,
        session: Session,
        url: str = "",
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        max_body: int = _MAX_BODY_CHARS,
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult

        if not url:
            return self._make_failure("fetch", "empty_url")

        base_url = getattr(session, "_current_url", "") or ""
        resolved = urljoin(base_url, url) if base_url else url

        # Same policy gate as navigation — domain rules apply to agent
        # requests too.
        policy_failure = self._check_policy(session, "fetch", url=resolved)
        if policy_failure is not None:
            return policy_failure

        network = getattr(session, "network", None)
        if network is None:
            return self._make_failure("fetch", "network_not_initialized")

        req_headers = dict(headers or {})
        req_headers.setdefault("Referer", base_url)
        body_bytes = body.encode("utf-8") if isinstance(body, str) else None

        try:
            resp = await network.fetch(
                resolved, method=method.upper(),
                headers=req_headers, body=body_bytes,
                resource_type="fetch",
            )
        except Exception as exc:
            return self._make_failure(
                "fetch", f"request_error: {exc}", target=resolved
            )

        text = resp.text
        limit = max(0, min(max_body, _MAX_BODY_CHARS))
        truncated = len(text) > limit
        clipped = text[:limit]

        content_type = (resp.headers or {}).get("content-type", "")
        parsed_json: Any = None
        if "json" in content_type:
            try:
                parsed_json = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed_json = None

        # Record in the session network log alongside page-initiated
        # requests, marked as agent-initiated.
        from an_web.actions.navigate import _record_network_activity
        _record_network_activity(session, {
            "url": resp.url,
            "method": method.upper(),
            "kind": "agent",
            "status": resp.status,
            "ok": resp.ok,
            "content_type": content_type,
            "body": text,
            "error": None,
            "elapsed_ms": getattr(resp, "elapsed_ms", 0),
        })

        return ActionResult(
            status="ok",
            action="fetch",
            target=resolved,
            effects={
                "status": resp.status,
                "ok": resp.ok,
                "url": resp.url,
                "content_type": content_type,
                "body": clipped,
                "json": parsed_json,
                "truncated": truncated,
            },
        )
