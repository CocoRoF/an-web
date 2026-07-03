"""SSR-preservation fallback: JS that wipes server-rendered content must not
leave the agent with an empty shell (v0.9.0)."""
from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from an_web import ANWebEngine

FILLER = "server rendered paragraph with plenty of visible text. " * 40

WIPE_HTML = f"""<!doctype html><html><head><title>wipe</title></head><body>
<main id="content"><h1>Real Article</h1><p>{FILLER}</p></main>
<script>
  // SPA boot that replaces everything with an empty app shell
  document.body.innerHTML = '';
  var root = document.createElement('div');
  root.id = 'app';
  document.body.appendChild(root);
</script>
</body></html>""".encode()

KEEP_HTML = f"""<!doctype html><html><head><title>keep</title></head><body>
<main><h1>Real Article</h1><p>{FILLER}</p></main>
<script>
  var extra = document.createElement('p');
  extra.textContent = 'client-side addition';
  document.body.appendChild(extra);
</script>
</body></html>""".encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = WIPE_HTML if self.path.startswith("/wipe") else KEEP_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_wiped_body_restores_pre_js_dom(server):
    async with ANWebEngine() as engine:
        s = await engine.create_session()
        nav = await s.navigate(f"{server}/wipe")
        assert nav["effects"]["dom_restored"] is True
        r = await s.act({"tool": "extract", "query": "h1"})
        texts = [x["text"] for x in r["effects"]["results"]]
        assert "Real Article" in texts
        await s.close()


async def test_normal_page_not_restored(server):
    async with ANWebEngine() as engine:
        s = await engine.create_session()
        nav = await s.navigate(f"{server}/keep")
        assert nav["effects"]["dom_restored"] is False
        r = await s.act({"tool": "extract", "query": "h1"})
        assert any("Real Article" in x["text"] for x in r["effects"]["results"])
        await s.close()
