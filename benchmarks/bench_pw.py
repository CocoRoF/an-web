"""Playwright side of the comparison bench. Mirrors bench_anweb.py scenario-for-scenario."""
import asyncio
import time
import traceback

from bench_common import MICRO_ROUNDS, SITES, Recorder, Timer
from playwright.async_api import async_playwright

NAV_TIMEOUT_MS = 90_000


async def main():
    rec = Recorder("playwright")
    snapshots = {}

    with Timer() as t_cold:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        _ = await ctx.new_page()  # include first-page cost in cold start
    cold_ms = t_cold.ms

    async def scenario(name, coro):
        with Timer() as t:
            try:
                ok, extra = await asyncio.wait_for(coro, timeout=120)
            except Exception as e:
                ok, extra = False, {"error": f"{type(e).__name__}: {e}",
                                    "trace": traceback.format_exc()[-400:]}
        rec.add(name, ok, t.ms, **extra)

    async def texts(pg, sel, limit=40):
        vals = await pg.locator(sel).all_inner_texts()
        return [v.strip() for v in vals[:limit]]

    # S1 static -------------------------------------------------------------
    async def s1():
        pg = await ctx.new_page()
        await pg.goto(SITES["static"], timeout=NAV_TIMEOUT_MS)
        h1 = await texts(pg, "h1")
        title = await pg.title()
        await pg.close()
        return (bool(h1) and "Example Domain" in h1[0]), {"h1": h1[:1], "title": title}
    await scenario("static", s1())

    # S2 wiki ---------------------------------------------------------------
    async def s2():
        pg = await ctx.new_page()
        await pg.goto(SITES["wiki"], timeout=NAV_TIMEOUT_MS)
        h1 = await texts(pg, "h1")
        paras = await texts(pg, "#mw-content-text p", limit=5)
        body = next((p for p in paras if len(p) > 100), "")
        snapshots["wiki"] = await pg.locator("body").aria_snapshot()
        await pg.close()
        ok = bool(h1) and "Python" in h1[0] and len(body) > 100
        return ok, {"h1": h1[:1], "para_sample": body[:120]}
    await scenario("wiki", s2())

    # S3 hn -----------------------------------------------------------------
    async def s3():
        pg = await ctx.new_page()
        await pg.goto(SITES["hn"], timeout=NAV_TIMEOUT_MS)
        titles = [t for t in await texts(pg, "span.titleline > a", limit=30) if t]
        snapshots["hn"] = await pg.locator("body").aria_snapshot()
        await pg.close()
        return len(titles) >= 10, {"count": len(titles), "sample": titles[:5]}
    await scenario("hn", s3())

    # S4 spa (Next.js) --------------------------------------------------------
    async def s4():
        pg = await ctx.new_page()
        await pg.goto(SITES["spa"], timeout=NAV_TIMEOUT_MS, wait_until="networkidle")
        links = [t for t in await texts(pg, "a", limit=100) if t]
        paras = [t for t in await texts(pg, "p,h1,h2,h3", limit=100) if t]
        text_len = sum(len(x) for x in paras)
        snapshots["spa"] = await pg.locator("body").aria_snapshot()
        await pg.close()
        ok = len(links) >= 5 and text_len > 300
        return ok, {"links": len(links), "text_len": text_len, "link_sample": links[:8]}
    await scenario("spa", s4())

    # S5 form ---------------------------------------------------------------
    async def s5():
        pg = await ctx.new_page()
        await pg.goto(SITES["form"], timeout=NAV_TIMEOUT_MS)
        await pg.fill("input[name='custname']", "HR Test")
        await pg.fill("input[name='custtel']", "010-1234-5678")
        async with pg.expect_navigation(timeout=NAV_TIMEOUT_MS):
            await pg.click("button")
        body = await pg.inner_text("body")
        await pg.close()
        return "HR Test" in body, {"echo": body[:150]}
    await scenario("form", s5())

    # S6 api (evaluate awaited fetch) -----------------------------------------
    async def s6():
        pg = await ctx.new_page()
        await pg.goto(SITES["api_base"], timeout=NAV_TIMEOUT_MS)
        val = await pg.evaluate("fetch('/json').then(r => r.json())")
        await pg.close()
        ok = isinstance(val, dict) and "slideshow" in val
        return ok, {"keys": list(val.keys()) if isinstance(val, dict) else str(val)[:80]}
    await scenario("api", s6())

    # S7 micro action latency -------------------------------------------------
    async def s7():
        pg = await ctx.new_page()
        await pg.goto(SITES["static"], timeout=NAV_TIMEOUT_MS)
        await texts(pg, "h1")  # warm
        t0 = time.perf_counter()
        for _ in range(MICRO_ROUNDS):
            await texts(pg, "h1")
        per = (time.perf_counter() - t0) * 1000 / MICRO_ROUNDS
        await pg.close()
        return True, {"per_action_ms": round(per, 2)}
    await scenario("micro", s7())

    await browser.close()
    await pw.stop()

    rec.finish("results_pw.json", cold_start_ms=round(cold_ms, 1),
               snapshots={k: {"chars": len(v), "head": v[:1200]} for k, v in snapshots.items()})


asyncio.run(main())
