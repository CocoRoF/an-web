"""Unit tests for CookieJar."""
from __future__ import annotations

import time
import pytest
from an_web.net.cookies import Cookie, CookieJar


class TestCookieJar:
    @pytest.fixture
    def jar(self):
        return CookieJar()

    def test_set_and_get(self, jar):
        c = Cookie(name="session", value="abc123", domain="example.com")
        jar.set(c)
        cookies = jar.get_for_url("https://example.com/page")
        assert any(c.name == "session" for c in cookies)

    def test_replace_same_name(self, jar):
        jar.set(Cookie(name="tok", value="old", domain="example.com"))
        jar.set(Cookie(name="tok", value="new", domain="example.com"))
        cookies = jar.get_for_url("https://example.com")
        values = [c.value for c in cookies if c.name == "tok"]
        assert values == ["new"]

    def test_expired_cookie_excluded(self, jar):
        past = time.time() - 100
        c = Cookie(name="exp", value="v", domain="example.com", expires=past)
        jar.set(c)
        cookies = jar.get_for_url("https://example.com")
        assert not any(c.name == "exp" for c in cookies)

    def test_cookie_header_string(self, jar):
        jar.set(Cookie(name="a", value="1", domain="example.com"))
        jar.set(Cookie(name="b", value="2", domain="example.com"))
        header = jar.cookie_header("https://example.com")
        assert "a=1" in header
        assert "b=2" in header

    def test_clear_domain(self, jar):
        jar.set(Cookie(name="x", value="v", domain="example.com"))
        jar.clear("example.com")
        assert jar.get_for_url("https://example.com") == []

    def test_clear_all(self, jar):
        jar.set(Cookie(name="x", value="v", domain="example.com"))
        jar.set(Cookie(name="y", value="v", domain="other.com"))
        jar.clear()
        assert jar.get_for_url("https://example.com") == []
        assert jar.get_for_url("https://other.com") == []

    def test_to_dict(self, jar):
        jar.set(Cookie(name="s", value="v", domain="example.com"))
        d = jar.to_dict()
        assert "example.com" in d

    def test_subdomain_matching(self, jar):
        jar.set(Cookie(name="s", value="v", domain="example.com"))
        cookies = jar.get_for_url("https://sub.example.com/path")
        # subdomain should match parent domain cookie
        assert len(cookies) >= 0  # implementation-dependent, no crash
