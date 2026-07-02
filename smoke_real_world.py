"""Real-world smoke test: navigate/snapshot/extract against live sites."""
import asyncio
import time
import traceback

from an_web import ANWebEngine

SITES = [
    ("example.com", "https://example.com", "h1"),
    ("httpbin-html", "https://httpbin.org/html", "h1"),
    ("python.org", "https://www.python.org", "a"),
    ("hn", "https://news.ycombinator.com", "span.titleline > a"),
    ("naver", "https://www.naver.com", "a"),
    ("wikipedia", "https://en.wikipedia.org/wiki/Python_(programming_language)", "h1"),
    ("hrletsgo", "https://hrletsgo.me", "a"),
]


async def test_site(engine, name, url, sel):
    t0 = time.time()
    out = {"name": name, "url": url}
    try:
        session = await engine.create_session()
        nav = await asyncio.wait_for(session.navigate(url), timeout=60)
        out["nav_status"] = nav.get("status")
        out["nav_error"] = nav.get("error")
        eff = nav.get("effects", {})
        out["status_code"] = eff.get("status_code")
        out["scripts"] = eff.get("scripts_executed")
        snap = await asyncio.wait_for(session.snapshot(), timeout=60)
        out["title"] = (snap.title or "")[:60]
        out["page_type"] = snap.page_type
        out["actions"] = len(snap.primary_actions or [])
        ext = await asyncio.wait_for(session.act({"tool": "extract", "query": sel}), timeout=60)
        out["extract_status"] = ext.get("status")
        out["extract_count"] = ext.get("effects", {}).get("count")
        results = ext.get("effects", {}).get("results", [])
        out["first_result"] = (results[0].get("text", "")[:50] if results else None)
        await session.close()
    except Exception as e:
        out["EXC"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()[-800:]
    out["elapsed"] = round(time.time() - t0, 2)
    return out


async def main():
    async with ANWebEngine() as engine:
        for name, url, sel in SITES:
            r = await test_site(engine, name, url, sel)
            print("=" * 70)
            for k, v in r.items():
                if k != "trace":
                    print(f"  {k}: {v}")
            if "trace" in r:
                print(r["trace"])


asyncio.run(main())
