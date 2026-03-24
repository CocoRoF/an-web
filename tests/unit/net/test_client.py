"""Unit tests for NetworkClient, Response, TraceEntry, NetworkTrace."""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from an_web.net.client import (
    NetworkClient,
    NetworkError,
    NetworkTrace,
    Response,
    TraceEntry,
    _classify_resource_type,
    _status_text,
)
from an_web.net.cookies import Cookie, CookieJar


# ─── Response ─────────────────────────────────────────────────────────────────

class TestResponse:
    def _make(self, **kw) -> Response:
        defaults = dict(
            url="https://example.com",
            status=200,
            headers={"content-type": "text/html; charset=utf-8"},
            body=b"<html>hello</html>",
        )
        defaults.update(kw)
        return Response(**defaults)

    def test_text_decoding_utf8(self):
        r = self._make(body="héllo".encode("utf-8"))
        assert r.text == "héllo"

    def test_text_charset_from_content_type(self):
        r = self._make(
            headers={"content-type": "text/html; charset=latin-1"},
            body="caf\xe9".encode("latin-1"),
        )
        assert "caf" in r.text

    def test_ok_2xx(self):
        assert self._make(status=200).ok is True
        assert self._make(status=201).ok is True
        assert self._make(status=301).ok is True  # redirects are "ok"
        assert self._make(status=399).ok is True

    def test_ok_4xx_false(self):
        assert self._make(status=404).ok is False
        assert self._make(status=500).ok is False

    def test_is_html(self):
        assert self._make(headers={"content-type": "text/html"}).is_html is True
        assert self._make(headers={"content-type": "application/json"}).is_html is False

    def test_content_type_strips_params(self):
        r = self._make(headers={"content-type": "text/html; charset=utf-8"})
        assert r.content_type == "text/html"

    def test_to_har_response(self):
        r = self._make(status=200, redirect_count=1, url="https://b.com")
        har = r.to_har_response()
        assert har["status"] == 200
        assert har["statusText"] == "OK"
        assert har["redirectURL"] == "https://b.com"
        assert "size" in har["content"]


# ─── TraceEntry ───────────────────────────────────────────────────────────────

class TestTraceEntry:
    def _make(self, **kw) -> TraceEntry:
        defaults = dict(
            timestamp=1000.0,
            method="GET",
            url="https://example.com",
            request_headers={},
            request_body=None,
            response_status=200,
            response_headers={"content-type": "text/html"},
            response_body_size=1024,
            resource_type="document",
            elapsed_ms=120.0,
        )
        defaults.update(kw)
        return TraceEntry(**defaults)

    def test_to_dict_structure(self):
        e = self._make()
        d = e.to_dict()
        assert d["method"] == "GET"
        assert d["url"] == "https://example.com"
        assert d["response"]["status"] == 200
        assert d["timings"]["total"] == 120.0
        assert d["resourceType"] == "document"
        assert d["error"] is None

    def test_error_recorded(self):
        e = self._make(response_status=0, error="timeout")
        d = e.to_dict()
        assert d["error"] == "timeout"


# ─── NetworkTrace ─────────────────────────────────────────────────────────────

class TestNetworkTrace:
    def _entry(self, resource_type: str = "document", size: int = 100, ms: float = 50.0):
        return TraceEntry(
            timestamp=0.0, method="GET", url="https://x.com",
            request_headers={}, request_body=None,
            response_status=200, response_headers={},
            response_body_size=size, resource_type=resource_type,
            elapsed_ms=ms,
        )

    def test_record_and_count(self):
        t = NetworkTrace()
        t.record(self._entry())
        t.record(self._entry())
        assert len(t.entries) == 2

    def test_filter_by_type(self):
        t = NetworkTrace()
        t.record(self._entry("document"))
        t.record(self._entry("script"))
        t.record(self._entry("document"))
        docs = t.filter_by_type("document")
        assert len(docs) == 2

    def test_total_bytes(self):
        t = NetworkTrace()
        t.record(self._entry(size=100))
        t.record(self._entry(size=250))
        assert t.total_bytes() == 350

    def test_total_time(self):
        t = NetworkTrace()
        t.record(self._entry(ms=80.0))
        t.record(self._entry(ms=40.0))
        assert t.total_time_ms() == pytest.approx(120.0)

    def test_to_har_structure(self):
        t = NetworkTrace()
        t.record(self._entry())
        har = t.to_har()
        assert har["log"]["version"] == "1.2"
        assert har["log"]["creator"]["name"] == "AN-Web"
        assert len(har["log"]["entries"]) == 1


# ─── NetworkClient (mocked with respx) ────────────────────────────────────────

class TestNetworkClientGet:
    @pytest.mark.asyncio
    @respx.mock
    async def test_simple_get(self):
        respx.get("https://example.com/page").mock(
            return_value=httpx.Response(
                200,
                content=b"<html>hello</html>",
                headers={"content-type": "text/html"},
            )
        )
        async with NetworkClient() as client:
            resp = await client.get("https://example.com/page")

        assert resp.status == 200
        assert resp.ok is True
        assert "hello" in resp.text
        assert resp.resource_type == "document"

    @pytest.mark.asyncio
    @respx.mock
    async def test_request_count_increments(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=b"hi")
        )
        async with NetworkClient() as client:
            await client.get("https://example.com/")
            await client.get("https://example.com/")
        assert client.request_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_response(self):
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404, content=b"Not found")
        )
        async with NetworkClient() as client:
            resp = await client.get("https://example.com/missing")
        assert resp.status == 404
        assert resp.ok is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_settled_after_request(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=b"hi")
        )
        async with NetworkClient() as client:
            assert client.is_settled() is True
            await client.get("https://example.com/")
            assert client.is_settled() is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_headers_sent(self):
        route = respx.get("https://example.com/api").mock(
            return_value=httpx.Response(200, content=b'{"ok":true}')
        )
        async with NetworkClient() as client:
            await client.get("https://example.com/api", headers={"X-Custom": "value"})

        assert route.called
        req = route.calls.last.request
        assert req.headers.get("x-custom") == "value"


class TestNetworkClientPost:
    @pytest.mark.asyncio
    @respx.mock
    async def test_post_form_data(self):
        # httpx follows redirects automatically, so mock both the POST and redirect target
        route = respx.post("https://example.com/login").mock(
            return_value=httpx.Response(
                302,
                headers={"location": "https://example.com/dashboard", "content-type": "text/html"},
                content=b"",
            )
        )
        respx.get("https://example.com/dashboard").mock(
            return_value=httpx.Response(200, content=b"welcome", headers={"content-type": "text/html"})
        )
        async with NetworkClient() as client:
            resp = await client.post(
                "https://example.com/login",
                data={"email": "test@test.com", "password": "secret"},
            )
        assert route.called
        # After redirect, final status is 200 at /dashboard
        assert resp.status == 200
        assert resp.redirect_count >= 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_post_json(self):
        route = respx.post("https://api.example.com/data").mock(
            return_value=httpx.Response(
                201,
                content=b'{"id":1}',
                headers={"content-type": "application/json"},
            )
        )
        async with NetworkClient() as client:
            resp = await client.post("https://api.example.com/data", json={"key": "value"})
        assert resp.status == 201
        assert resp.resource_type == "xhr"  # classified from content-type


class TestNetworkClientCookies:
    @pytest.mark.asyncio
    @respx.mock
    async def test_cookies_injected(self):
        jar = CookieJar()
        jar.set(Cookie(name="session", value="abc123", domain="example.com"))

        route = respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=b"ok")
        )
        async with NetworkClient(cookie_jar=jar) as client:
            await client.get("https://example.com/")

        req = route.calls.last.request
        assert "session=abc123" in req.headers.get("cookie", "")

    @pytest.mark.asyncio
    @respx.mock
    async def test_set_cookie_harvested(self):
        jar = CookieJar()
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(
                200,
                content=b"ok",
                headers={"set-cookie": "token=xyz; Path=/; HttpOnly"},
            )
        )
        async with NetworkClient(cookie_jar=jar) as client:
            await client.get("https://example.com/")

        cookies = jar.get_for_url("https://example.com/")
        names = [c.name for c in cookies]
        assert "token" in names

    @pytest.mark.asyncio
    @respx.mock
    async def test_harvested_cookie_sent_next_request(self):
        jar = CookieJar()
        respx.get("https://example.com/login").mock(
            return_value=httpx.Response(
                200,
                content=b"ok",
                headers={"set-cookie": "session=s1; Path=/"},
            )
        )
        second_route = respx.get("https://example.com/dashboard").mock(
            return_value=httpx.Response(200, content=b"dashboard")
        )
        async with NetworkClient(cookie_jar=jar) as client:
            await client.get("https://example.com/login")
            await client.get("https://example.com/dashboard")

        req = second_route.calls.last.request
        assert "session=s1" in req.headers.get("cookie", "")


class TestNetworkClientTrace:
    @pytest.mark.asyncio
    @respx.mock
    async def test_trace_records_request(self):
        respx.get("https://example.com/").mock(
            return_value=httpx.Response(200, content=b"hi", headers={"content-type": "text/html"})
        )
        async with NetworkClient() as client:
            await client.get("https://example.com/")
            trace = client.trace

        assert len(trace.entries) == 1
        e = trace.entries[0]
        assert e.method == "GET"
        assert e.response_status == 200
        assert e.resource_type == "document"

    @pytest.mark.asyncio
    @respx.mock
    async def test_trace_multiple_requests(self):
        respx.get("https://example.com/a").mock(return_value=httpx.Response(200, content=b"a"))
        respx.get("https://example.com/b").mock(return_value=httpx.Response(200, content=b"b"))
        async with NetworkClient() as client:
            await client.get("https://example.com/a")
            await client.get("https://example.com/b")

        assert len(client.trace.entries) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_trace_har_export(self):
        respx.get("https://example.com/").mock(return_value=httpx.Response(200, content=b"x"))
        async with NetworkClient() as client:
            await client.get("https://example.com/")
            har = client.trace.to_har()

        assert har["log"]["version"] == "1.2"
        entries = har["log"]["entries"]
        assert len(entries) == 1


class TestNetworkClientErrors:
    @pytest.mark.asyncio
    @respx.mock
    async def test_timeout_raises_network_error(self):
        respx.get("https://slow.example.com/").mock(side_effect=httpx.TimeoutException("timed out"))
        async with NetworkClient() as client:
            with pytest.raises(NetworkError) as exc_info:
                await client.get("https://slow.example.com/")
        assert exc_info.value.url == "https://slow.example.com/"

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_raises_network_error(self):
        respx.get("https://unreachable.example.com/").mock(
            side_effect=httpx.ConnectError("refused")
        )
        async with NetworkClient() as client:
            with pytest.raises(NetworkError):
                await client.get("https://unreachable.example.com/")

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_recorded_in_trace(self):
        respx.get("https://fail.example.com/").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        async with NetworkClient() as client:
            try:
                await client.get("https://fail.example.com/")
            except NetworkError:
                pass
            entries = client.trace.entries

        assert len(entries) == 1
        assert entries[0].error is not None


# ─── Helpers ──────────────────────────────────────────────────────────────────

class TestClassifyResourceType:
    def test_html(self):
        assert _classify_resource_type("text/html", "document") == "document"

    def test_javascript(self):
        assert _classify_resource_type("application/javascript", "document") == "script"
        assert _classify_resource_type("text/javascript", "document") == "script"

    def test_css(self):
        assert _classify_resource_type("text/css", "document") == "stylesheet"

    def test_image(self):
        assert _classify_resource_type("image/png", "document") == "image"

    def test_json_returns_xhr(self):
        assert _classify_resource_type("application/json", "document") == "xhr"

    def test_unknown_uses_hint(self):
        assert _classify_resource_type("application/octet-stream", "fetch") == "fetch"
        assert _classify_resource_type("", "xhr") == "xhr"


class TestStatusText:
    def test_known(self):
        assert _status_text(200) == "OK"
        assert _status_text(404) == "Not Found"
        assert _status_text(500) == "Internal Server Error"
        assert _status_text(302) == "Found"

    def test_unknown(self):
        assert _status_text(999) == "Unknown"


class TestResolveUrl:
    def test_absolute_unchanged(self):
        assert NetworkClient.resolve_url("https://a.com/", "https://b.com/x") == "https://b.com/x"

    def test_relative_path(self):
        assert NetworkClient.resolve_url("https://a.com/page", "/other") == "https://a.com/other"

    def test_relative_to_base(self):
        result = NetworkClient.resolve_url("https://a.com/dir/page", "sub")
        assert result == "https://a.com/dir/sub"

    def test_protocol_relative(self):
        result = NetworkClient.resolve_url("https://a.com/", "//cdn.com/script.js")
        assert result == "//cdn.com/script.js"
