"""Cookie jar management for AN-Web."""
from __future__ import annotations

from dataclasses import dataclass, field
from http.cookiejar import CookieJar as StdCookieJar
import time


@dataclass
class Cookie:
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: float | None = None
    secure: bool = False
    http_only: bool = False
    same_site: str = "Lax"
    max_age: int | None = None          # seconds; takes priority over expires
    expires_raw: str = ""               # raw Expires= string (parsed lazily)

    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        return time.time() > self.expires

    def to_header(self) -> str:
        return f"{self.name}={self.value}"


class CookieJar:
    """Per-session cookie storage."""

    def __init__(self) -> None:
        self._cookies: dict[str, list[Cookie]] = {}  # domain → cookies

    def set(self, cookie: Cookie) -> None:
        domain = cookie.domain.lower()
        if domain not in self._cookies:
            self._cookies[domain] = []
        # Replace existing cookie with same name
        self._cookies[domain] = [
            c for c in self._cookies[domain] if c.name != cookie.name
        ]
        self._cookies[domain].append(cookie)

    def get_for_url(self, url: str) -> list[Cookie]:
        """Return non-expired cookies matching the URL's domain."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""

        matching: list[Cookie] = []
        for domain, cookies in self._cookies.items():
            if host.endswith(domain) or domain.endswith(host):
                for c in cookies:
                    if not c.is_expired():
                        matching.append(c)
        return matching

    def cookie_header(self, url: str) -> str:
        """Build Cookie header string for a URL."""
        cookies = self.get_for_url(url)
        return "; ".join(c.to_header() for c in cookies)

    def clear(self, domain: str | None = None) -> None:
        if domain:
            self._cookies.pop(domain.lower(), None)
        else:
            self._cookies.clear()

    def to_dict(self) -> dict:
        return {
            domain: [
                {"name": c.name, "value": c.value, "path": c.path,
                 "expires": c.expires, "secure": c.secure}
                for c in cookies
            ]
            for domain, cookies in self._cookies.items()
        }
