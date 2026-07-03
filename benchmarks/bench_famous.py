"""Famous-sites bench: an-web vs Playwright on the same 10 real-world sites.

Usage: python bench_famous.py anweb|pw
Writes famous_<engine>.json. Success criteria per site:
  - title non-empty
  - visible text length >= threshold
  - at least `min_links` links extracted
Everything is recorded honestly — failures stay failures.
"""
import asyncio
import sys
import time
import traceback

from bench_common import Recorder, Timer, tree_rss_mb

SITES = [
    # (key, url, text_threshold, min_links)
    ("example",   "https://example.com",                                  100,  1),
    ("wikipedia", "https://en.wikipedia.org/wiki/Artificial_intelligence", 1000, 20),
    ("hn",        "https://news.ycombinator.com",                          500, 30),
    ("github",    "https://github.com",                                    300, 10),
    ("stackoverflow", "https://stackoverflow.com",                         300, 10),
    ("mdn",       "https://developer.mozilla.org",                         300, 10),
    ("python",    "https://www.python.org",                                500, 20),
    ("naver",     "https://www.naver.com",                                 300, 20),
    ("bbc",       "https://www.bbc.com",                                   500, 20),
    ("hrletsgo",  "https://hrletsgo.me",                                   300,  5),
]

NAV_TIMEOUT_S = 60


async def run_anweb(rec):
    from an_web import ANWebEngine

    with Timer() as t_cold:
        engine = ANWebEngine()
        await engine.__aenter__()
    rec_cold = t_cold.ms

    for key, url, text_thr, min_links in SITES:
        t0 = time.perf_counter()
        if True:
            try:
                s = await engine.create_session()
                nav = await asyncio.wait_for(s.navigate(url), timeout=NAV_TIMEOUT_S + 30)
                snap = await s.snapshot()
                title = snap.title or ""
                ex = await s.act({"tool": "extract", "query": "h1,h2,h3,p"})
                paras = [x.get("text", "") for x in ex["effects"].get("results", [])]
                text_len = sum(len(p) for p in paras)
                exl = await s.act({"tool": "extract", "query": "a"})
                links = [x.get("text", "").strip() for x in exl["effects"].get("results", [])]
                links = [x for x in links if x]
                headline = next((p.strip() for p in paras if len(p.strip()) > 40), "")
                status_code = (nav.get("effects") or {}).get("status_code")
                await s.close()
                ok = bool(title) and text_len >= text_thr and len(links) >= min_links
                rec.add(key, ok, (time.perf_counter() - t0) * 1000,
                        title=title[:60], status_code=status_code,
                        text_len=text_len, links=len(links), headline=headline[:100])
            except Exception as e:
                rec.add(key, False, (time.perf_counter() - t0) * 1000,
                        error=f"{type(e).__name__}: {e}"[:200],
                        trace=traceback.format_exc()[-300:])
        rec.sample_mem()

    await engine.__aexit__(None, None, None)
    return rec_cold


async def run_pw(rec):
    from playwright.async_api import async_playwright

    with Timer() as t_cold:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
    rec_cold = t_cold.ms

    for key, url, text_thr, min_links in SITES:
        t0 = time.perf_counter()
        if True:
            try:
                pg = await ctx.new_page()
                resp = await pg.goto(url, timeout=NAV_TIMEOUT_S * 1000,
                                     wait_until="domcontentloaded")
                try:
                    await pg.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass  # busy sites never go idle; proceed
                title = await pg.title()
                paras = await pg.locator("h1,h2,h3,p").all_inner_texts()
                text_len = sum(len(p) for p in paras)
                links = [x.strip() for x in await pg.locator("a").all_inner_texts() if x.strip()]
                headline = next((p.strip() for p in paras if len(p.strip()) > 40), "")
                status_code = resp.status if resp else None
                await pg.close()
                ok = bool(title) and text_len >= text_thr and len(links) >= min_links
                rec.add(key, ok, (time.perf_counter() - t0) * 1000,
                        title=title[:60], status_code=status_code,
                        text_len=text_len, links=len(links), headline=headline[:100])
            except Exception as e:
                try:
                    await pg.close()
                except Exception:
                    pass
                rec.add(key, False, (time.perf_counter() - t0) * 1000,
                        error=f"{type(e).__name__}: {e}"[:200])
        rec.sample_mem()

    await browser.close()
    await pw.stop()
    return rec_cold


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "anweb"
    rec = Recorder("an-web" if which == "anweb" else "playwright")
    baseline = tree_rss_mb()
    cold = await (run_anweb(rec) if which == "anweb" else run_pw(rec))
    rec.finish(f"famous_{which}.json", cold_start_ms=round(cold, 1),
               baseline_rss_mb=baseline)


asyncio.run(main())
