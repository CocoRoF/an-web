"""Unit tests for net/loader.py — ResourceLoader."""
from __future__ import annotations

import pytest
import httpx
import respx

from an_web.net.client import NetworkClient
from an_web.net.loader import ResourceLoader
from an_web.net.resources import LoadPolicy, ResourceType


BASE = "http://loader.test"


@pytest.fixture
def client():
    return NetworkClient()


class TestResourceLoaderInit:
    def test_default_policy(self, client):
        loader = ResourceLoader(client)
        assert loader.client is client
        assert isinstance(loader.policy, LoadPolicy)
        assert ResourceType.DOCUMENT in loader.policy.allowed_types

    def test_custom_policy(self, client):
        policy = LoadPolicy(allowed_types={ResourceType.IMAGE})
        loader = ResourceLoader(client, policy=policy)
        assert loader.policy is policy


class TestResourceLoaderLoadDocument:
    @pytest.mark.asyncio
    @respx.mock
    async def test_load_document_returns_response(self, client):
        respx.get(f"{BASE}/page.html").mock(
            return_value=httpx.Response(200, content=b"<html><body>hello</body></html>",
                                        headers={"content-type": "text/html"})
        )
        loader = ResourceLoader(client)
        async with client:
            resp = await loader.load_document(f"{BASE}/page.html")
        assert resp.status == 200

    @pytest.mark.asyncio
    @respx.mock
    async def test_load_document_404(self, client):
        respx.get(f"{BASE}/missing.html").mock(
            return_value=httpx.Response(404, content=b"Not Found",
                                        headers={"content-type": "text/html"})
        )
        loader = ResourceLoader(client)
        async with client:
            resp = await loader.load_document(f"{BASE}/missing.html")
        assert resp.status == 404


class TestResourceLoaderLoadScript:
    @pytest.mark.asyncio
    @respx.mock
    async def test_load_script_allowed_by_default(self, client):
        respx.get(f"{BASE}/app.js").mock(
            return_value=httpx.Response(200, content=b"console.log('hi')",
                                        headers={"content-type": "application/javascript"})
        )
        loader = ResourceLoader(client)
        async with client:
            resp = await loader.load_script(f"{BASE}/app.js")
        assert resp is not None
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_load_script_blocked_by_policy(self, client):
        policy = LoadPolicy(allowed_types={ResourceType.DOCUMENT})  # no SCRIPT
        loader = ResourceLoader(client, policy=policy)
        async with client:
            resp = await loader.load_script(f"{BASE}/app.js")
        assert resp is None


class TestResourceLoaderLoadStylesheet:
    @pytest.mark.asyncio
    @respx.mock
    async def test_load_stylesheet_blocked_by_default(self, client):
        loader = ResourceLoader(client)  # default policy: no STYLESHEET
        async with client:
            resp = await loader.load_stylesheet(f"{BASE}/style.css")
        assert resp is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_load_stylesheet_allowed_with_custom_policy(self, client):
        respx.get(f"{BASE}/style.css").mock(
            return_value=httpx.Response(200, content=b"body { color: red; }",
                                        headers={"content-type": "text/css"})
        )
        policy = LoadPolicy()
        policy.allow_styles()
        loader = ResourceLoader(client, policy=policy)
        async with client:
            resp = await loader.load_stylesheet(f"{BASE}/style.css")
        assert resp is not None
        assert resp.status == 200
