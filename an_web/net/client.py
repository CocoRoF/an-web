"""
Network client for AN-Web.

httpx.AsyncClient-based HTTP layer (vs Lightpanda's libcurl/HttpClient.zig).

Key features:
- Async GET/POST with automatic redirect following
- Cookie jar integration (inject + harvest Set-Cookie)
- Resource-type classification from Content-Type
- HAR-like per-request trace entries for debugging and replay
- Pending-request counter for event-loop settlement detection
- Clean error wrapping into typed NetworkError
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx

if TYPE_CHECKING:
    from an_web.net.cookies import CookieJar


# ─── Domain errors ────────────────────────────────────────────────────────────

class NetworkError(Exception):
    """Raised for unrecoverable fetch errors (timeout, DNS failure, etc.)."""

    def __init__(self, message: str, url: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.cause = cause


# ─── Response ─────────────────────────────────────────────────────────────────

@dataclass
class Response:
    """A fully-resolved HTTP response including decoded body and metadata."""

    url: str                        # Final URL after redirects
    status: int
    headers: dict[str, str]
    body: bytes
    resource_type: str = "document"
    elapsed_ms: float = 0.0
    redirect_count: int = 0
    request_url: str = ""           # Original requested URL (before redirects)

    @property
    def text(self) -> str:
        encoding = "utf-8"
        ct = self.headers.get("content-type", "")
        if "charset=" in ct:
            encoding = ct.split("charset=")[-1].split(";")[0].strip()
        try:
            return self.body.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return self.body.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip()

    @property
    def is_html(self) -> bool:
        ct = self.headers.get("content-type", "").lower()
        return "html" in ct

    def to_har_response(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "statusText": _status_text(self.status),
            "headers": [{"name": k, "value": v} for k, v in self.headers.items()],
            "content": {
                "size": len(self.body),
                "mimeType": self.content_type,
            },
            "redirectURL": self.url if self.redirect_count > 0 else "",
        }


# ─── HAR-like trace entry ──────────────────────────────────────────────────────

@dataclass
class TraceEntry:
    """
    Single HTTP request/response record — mirrors HAR (HTTP Archive) format.

    Useful for:
    - Replay: re-run the exact same sequence of requests
    - Debug: post-mortem inspection of what was loaded and when
    - Policy: detect blocked/redirected resources
    """

    timestamp: float
    method: str
    url: str
    request_headers: dict[str, str]
    request_body: bytes | None
    response_status: int
    response_headers: dict[str, str]
    response_body_size: int
    resource_type: str
    elapsed_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "method": self.method,
            "url": self.url,
            "request": {
                "headers": self.request_headers,
                "bodySize": len(self.request_body) if self.request_body else 0,
            },
            "response": {
                "status": self.response_status,
                "statusText": _status_text(self.response_status),
                "headers": self.response_headers,
                "bodySize": self.response_body_size,
                "mimeType": self.response_headers.get("content-type", ""),
            },
            "resourceType": self.resource_type,
            "timings": {"total": self.elapsed_ms},
            "error": self.error,
        }


@dataclass
class NetworkTrace:
    """Accumulates all TraceEntry records for a page load session."""

    entries: list[TraceEntry] = field(default_factory=list)

    def record(self, entry: TraceEntry) -> None:
        self.entries.append(entry)

    def to_har(self) -> dict[str, Any]:
        """Export as HAR 1.2-compatible structure."""
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "AN-Web", "version": "0.1.0"},
                "entries": [e.to_dict() for e in self.entries],
            }
        }

    def filter_by_type(self, resource_type: str) -> list[TraceEntry]:
        return [e for e in self.entries if e.resource_type == resource_type]

    def total_bytes(self) -> int:
        return sum(e.response_body_size for e in self.entries)

    def total_time_ms(self) -> float:
        return sum(e.elapsed_ms for e in self.entries)


# ─── NetworkClient ─────────────────────────────────────────────────────────────

class NetworkClient:
    """
    Async HTTP client with cookie jar, redirect handling, and resource policy.

    Mirrors Lightpanda's HttpClient.zig but using httpx instead of libcurl.
    """

    DEFAULT_HEADERS = {
        "User-Agent": (
            "ANWeb/0.1 (AI-Native Browser Engine; "
            "https://github.com/CocoRoF/an-web)"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }

    def __init__(
        self,
        cookie_jar: CookieJar | None = None,
        timeout: float = 30.0,
        max_redirects: int = 10,
        trace: NetworkTrace | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self.cookie_jar = cookie_jar
        self.trace = trace or NetworkTrace()
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            max_redirects=max_redirects,
            headers=self.DEFAULT_HEADERS,
            verify=verify_ssl,
        )
        self._request_count = 0
        self._pending: int = 0

    # ── Public request methods ───────────────────────────────────────────────

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        resource_type: str = "document",
    ) -> Response:
        return await self._request("GET", url, headers=headers, resource_type=resource_type)

    async def post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        resource_type: str = "xhr",
    ) -> Response:
        return await self._request(
            "POST", url,
            data=data, json=json,
            headers=headers, resource_type=resource_type,
        )

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        resource_type: str = "fetch",
    ) -> Response:
        """Low-level fetch matching the JS Fetch API surface."""
        return await self._request(
            method.upper(), url,
            raw_body=body, headers=headers, resource_type=resource_type,
        )

    # ── Core request logic ───────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        resource_type: str = "document",
    ) -> Response:
        req_headers = dict(headers or {})

        # Inject cookies from jar
        if self.cookie_jar:
            cookie_str = self.cookie_jar.cookie_header(url)
            if cookie_str:
                req_headers["Cookie"] = cookie_str

        # Track the original URL before redirects
        original_url = url

        self._pending += 1
        self._request_count += 1
        t0 = time.monotonic()
        try:
            httpx_resp = await self._client.request(
                method,
                url,
                data=data,
                json=json,
                content=raw_body,
                headers=req_headers,
            )
        except httpx.TimeoutException as exc:
            elapsed = (time.monotonic() - t0) * 1000
            self._record_error(method, url, req_headers, elapsed, str(exc))
            raise NetworkError(f"Request timed out: {url}", url=url, cause=exc) from exc
        except httpx.ConnectError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            self._record_error(method, url, req_headers, elapsed, str(exc))
            raise NetworkError(f"Connection failed: {url}", url=url, cause=exc) from exc
        except httpx.HTTPError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            self._record_error(method, url, req_headers, elapsed, str(exc))
            raise NetworkError(f"HTTP error: {exc}", url=url, cause=exc) from exc
        finally:
            self._pending -= 1

        elapsed = (time.monotonic() - t0) * 1000

        # Harvest Set-Cookie headers
        if self.cookie_jar:
            self._harvest_cookies(original_url, httpx_resp)

        # Determine resource type from Content-Type if not specified
        final_ct = httpx_resp.headers.get("content-type", "")
        actual_resource_type = _classify_resource_type(final_ct, resource_type)

        # Count redirects
        redirect_count = len(httpx_resp.history)

        resp = Response(
            url=str(httpx_resp.url),
            status=httpx_resp.status_code,
            headers=dict(httpx_resp.headers),
            body=httpx_resp.content,
            resource_type=actual_resource_type,
            elapsed_ms=elapsed,
            redirect_count=redirect_count,
            request_url=original_url,
        )

        # Record HAR trace entry
        self.trace.record(TraceEntry(
            timestamp=t0,
            method=method,
            url=str(httpx_resp.url),
            request_headers=req_headers,
            request_body=raw_body,
            response_status=httpx_resp.status_code,
            response_headers=dict(httpx_resp.headers),
            response_body_size=len(httpx_resp.content),
            resource_type=actual_resource_type,
            elapsed_ms=elapsed,
        ))

        return resp

    # ── Cookie management ────────────────────────────────────────────────────

    def _harvest_cookies(self, url: str, resp: httpx.Response) -> None:
        """Parse Set-Cookie response headers and store in cookie jar.

        Also checks redirect history responses so cookies set on 3xx redirects
        are captured correctly.
        """
        from an_web.net.cookies import Cookie

        # Collect (url, response) pairs: intermediate redirects + final response
        pairs: list[tuple[str, httpx.Response]] = []
        for hist_resp in getattr(resp, "history", []):
            pairs.append((str(hist_resp.url), hist_resp))
        pairs.append((str(resp.url), resp))

        for resp_url, r in pairs:
            domain = urlparse(resp_url).hostname or ""
            for set_cookie in r.headers.get_list("set-cookie"):
                parts = [p.strip() for p in set_cookie.split(";")]
                if not parts or not parts[0]:
                    continue
                name, _, value = parts[0].partition("=")
                name = name.strip()
                value = value.strip()
                if not name:
                    continue

                cookie = Cookie(name=name, value=value, domain=domain)
                for part in parts[1:]:
                    key, _, val = part.partition("=")
                    key = key.strip().lower()
                    if key == "path":
                        cookie.path = val.strip() or "/"
                    elif key == "domain":
                        cookie.domain = val.strip().lstrip(".")
                    elif key == "secure":
                        cookie.secure = True
                    elif key == "httponly":
                        cookie.http_only = True
                    elif key == "max-age":
                        try:
                            cookie.max_age = int(val.strip())
                        except ValueError:
                            pass
                    elif key == "expires":
                        cookie.expires_raw = val.strip()

                if self.cookie_jar:
                    self.cookie_jar.set(cookie)

    def _record_error(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        elapsed_ms: float,
        error: str,
    ) -> None:
        self.trace.record(TraceEntry(
            timestamp=time.monotonic(),
            method=method,
            url=url,
            request_headers=headers,
            request_body=None,
            response_status=0,
            response_headers={},
            response_body_size=0,
            resource_type="document",
            elapsed_ms=elapsed_ms,
            error=error,
        ))

    # ── URL utilities ────────────────────────────────────────────────────────

    @staticmethod
    def resolve_url(base: str, relative: str) -> str:
        """Resolve a potentially-relative URL against a base URL."""
        if relative.startswith(("http://", "https://", "//", "data:", "blob:")):
            return relative
        return urljoin(base, relative)

    # ── State ────────────────────────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return self._pending

    @property
    def request_count(self) -> int:
        return self._request_count

    def is_settled(self) -> bool:
        """True when all in-flight requests have completed."""
        return self._pending == 0

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> NetworkClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _classify_resource_type(content_type: str, hint: str) -> str:
    """Determine resource type from Content-Type header, using hint as fallback."""
    ct = content_type.lower()
    if "html" in ct:
        return "document"
    if "javascript" in ct or "ecmascript" in ct:
        return "script"
    if "css" in ct:
        return "stylesheet"
    if ct.startswith("image/"):
        return "image"
    if "font" in ct or "woff" in ct:
        return "font"
    if "json" in ct:
        return "xhr"
    # Use caller-provided hint if content-type doesn't clarify
    return hint


_HTTP_STATUS_TEXTS: dict[int, str] = {
    200: "OK", 201: "Created", 204: "No Content",
    301: "Moved Permanently", 302: "Found", 303: "See Other",
    304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout",
    429: "Too Many Requests", 500: "Internal Server Error",
    502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout",
}


def _status_text(status: int) -> str:
    return _HTTP_STATUS_TEXTS.get(status, "Unknown")
