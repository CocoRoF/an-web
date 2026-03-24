"""
Network client for AN-Web.

httpx.AsyncClient-based HTTP layer (vs Lightpanda's libcurl).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from an_web.net.cookies import CookieJar
    from an_web.net.resources import ResourceType


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    resource_type: str = "document"
    elapsed_ms: float = 0.0

    @property
    def text(self) -> str:
        encoding = "utf-8"
        ct = self.headers.get("content-type", "")
        if "charset=" in ct:
            encoding = ct.split("charset=")[-1].split(";")[0].strip()
        return self.body.decode(encoding, errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


class NetworkClient:
    """
    Async HTTP client with cookie jar, redirect handling, and resource policy.

    Corresponds to Lightpanda's HttpClient.zig functionality.
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "ANWeb/0.1 (AI-Native Browser Engine; "
            "https://github.com/CocoRoF/an-web)"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(
        self,
        cookie_jar: CookieJar | None = None,
        timeout: float = 30.0,
        max_redirects: int = 10,
    ) -> None:
        self.cookie_jar = cookie_jar
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            max_redirects=max_redirects,
            headers=self.DEFAULT_HEADERS,
        )
        self._request_count = 0
        self._pending: int = 0

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return await self._request("GET", url, headers=headers)

    async def post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        return await self._request("POST", url, data=data, json=json, headers=headers)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        import time
        req_headers = dict(headers or {})

        # Inject cookies
        if self.cookie_jar:
            cookie_str = self.cookie_jar.cookie_header(url)
            if cookie_str:
                req_headers["Cookie"] = cookie_str

        self._pending += 1
        self._request_count += 1
        t0 = time.monotonic()
        try:
            resp = await self._client.request(
                method,
                url,
                data=data,
                json=json,
                headers=req_headers,
            )
        finally:
            self._pending -= 1

        elapsed = (time.monotonic() - t0) * 1000

        # Update cookie jar from Set-Cookie headers
        if self.cookie_jar:
            self._update_cookies(url, resp)

        return Response(
            url=str(resp.url),
            status=resp.status_code,
            headers=dict(resp.headers),
            body=resp.content,
            elapsed_ms=elapsed,
        )

    def _update_cookies(self, url: str, resp: httpx.Response) -> None:
        from urllib.parse import urlparse
        from an_web.net.cookies import Cookie
        domain = urlparse(url).hostname or ""
        for set_cookie in resp.headers.get_list("set-cookie"):
            # Parse simple Set-Cookie header
            parts = [p.strip() for p in set_cookie.split(";")]
            if not parts:
                continue
            name, _, value = parts[0].partition("=")
            cookie = Cookie(name=name.strip(), value=value.strip(), domain=domain)
            for part in parts[1:]:
                key, _, val = part.partition("=")
                key = key.strip().lower()
                if key == "path":
                    cookie.path = val.strip()
                elif key == "secure":
                    cookie.secure = True
                elif key == "httponly":
                    cookie.http_only = True
            if self.cookie_jar:
                self.cookie_jar.set(cookie)

    @property
    def pending_count(self) -> int:
        return self._pending

    async def close(self) -> None:
        await self._client.aclose()
