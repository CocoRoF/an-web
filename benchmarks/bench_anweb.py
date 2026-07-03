"""an-web side of the comparison bench. Mirrors bench_pw.py scenario-for-scenario."""
import asyncio
import time
import traceback

from bench_common import MICRO_ROUNDS, SITES, Recorder, Timer

from an_web import ANWebEngine

NAV_TIMEOUT = 90


def render_tree(node, budget=400) -> str:
    """Replica of an-web-mcp's _render_node: the engine's real AI surface."""
    lines: list[str] = []
    b = [budget]

    def walk(n, depth):
        if b[0] <= 0:
            return
        role = n.role or n.tag or ""
        name = (n.name or "").strip()
        if len(name) > 80:
            name = name[:77] + "..."
        interactive = getattr(n, "is_interactive", False)
        skip = role in ("generic", "none", "presentation", "") and not name and not interactive
        if not skip:
            parts = [f"- {role}"]
            if name:
                parts.append(f' "{name}"')
            if interactive:
                parts.append(f" [ref={n.node_id}]")
            lines.append("  " * depth + "".join(parts))
            b[0] -= 1
            depth += 1
        for c in getattr(n, "children", []) or []:
            walk(c, depth)

    if node is not None:
        walk(node, 0)
    return "\n".join(lines)


async def texts(session, sel, limit=40):
    r = await session.act({"tool": "extract", "query": sel})
    res = (r.get("effects", {}) or {}).get("results", []) or []
    return [x.get("text", "").strip() for x in res[:limit]]


async def main():
    rec = Recorder("an-web")
    snapshots = {}

    with Timer() as t_cold:
        engine = ANWebEngine()
        await engine.__aenter__()
        session = await engine.create_session()
    cold_ms = t_cold.ms

    async def scenario(name, coro):
        with Timer() as t:
            try:
                ok, extra = await asyncio.wait_for(coro, timeout=NAV_TIMEOUT + 30)
            except Exception as e:
                ok, extra = False, {"error": f"{type(e).__name__}: {e}",
                                    "trace": traceback.format_exc()[-400:]}
        rec.add(name, ok, t.ms, **extra)

    # S1 static -------------------------------------------------------------
    async def s1():
        s = await engine.create_session()
        await s.navigate(SITES["static"])
        h1 = await texts(s, "h1")
        snap = await s.snapshot()
        await s.close()
        return (bool(h1) and "Example Domain" in h1[0]), {"h1": h1[:1], "title": snap.title}
    await scenario("static", s1())

    # S2 wiki ---------------------------------------------------------------
    async def s2():
        s = await engine.create_session()
        nav = await s.navigate(SITES["wiki"])
        h1 = await texts(s, "h1")
        paras = await texts(s, "#mw-content-text p", limit=5)
        body = next((p for p in paras if len(p) > 100), "")
        snap = await s.snapshot()
        snapshots["wiki"] = render_tree(snap.semantic_tree)
        await s.close()
        ok = bool(h1) and "Python" in h1[0] and len(body) > 100
        return ok, {"h1": h1[:1], "para_sample": body[:120],
                    "scripts": (nav.get("effects", {}) or {}).get("scripts_executed")}
    await scenario("wiki", s2())

    # S3 hn -----------------------------------------------------------------
    async def s3():
        s = await engine.create_session()
        await s.navigate(SITES["hn"])
        titles = await texts(s, "span.titleline > a", limit=30)
        titles = [t for t in titles if t]
        snap = await s.snapshot()
        snapshots["hn"] = render_tree(snap.semantic_tree)
        await s.close()
        return len(titles) >= 10, {"count": len(titles), "sample": titles[:5]}
    await scenario("hn", s3())

    # S4 spa (Next.js) --------------------------------------------------------
    async def s4():
        s = await engine.create_session()
        nav = await s.navigate(SITES["spa"])
        links = [t for t in await texts(s, "a", limit=100) if t]
        paras = [t for t in await texts(s, "p,h1,h2,h3", limit=100) if t]
        text_len = sum(len(x) for x in paras)
        snap = await s.snapshot()
        snapshots["spa"] = render_tree(snap.semantic_tree)
        await s.close()
        ok = len(links) >= 5 and text_len > 300
        return ok, {"links": len(links), "text_len": text_len,
                    "link_sample": links[:8],
                    "settle_ms": (nav.get("effects", {}) or {}).get("settle_timeout")}
    await scenario("spa", s4())

    # S5 form ---------------------------------------------------------------
    async def s5():
        s = await engine.create_session()
        await s.navigate(SITES["form"])
        await s.act({"tool": "type", "target": "input[name='custname']", "text": "HR Test"})
        await s.act({"tool": "type", "target": "input[name='custtel']", "text": "010-1234-5678"})
        sub = await s.act({"tool": "submit", "target": "form"})
        body = " ".join(await texts(s, "body,pre", limit=5))
        await s.close()
        ok = "HR Test" in body
        return ok, {"submit_status": sub.get("status"), "echo": body[:150]}
    await scenario("form", s5())

    # S6 api (eval_js awaited fetch) -----------------------------------------
    async def s6():
        s = await engine.create_session()
        await s.navigate(SITES["api_base"])
        r = await s.act({"tool": "eval_js",
                         "script": "fetch('/json').then(r => r.json())"})
        val = (r.get("effects", {}) or {}).get("raw_value")
        await s.close()
        ok = isinstance(val, dict) and "slideshow" in val
        return ok, {"keys": list(val.keys()) if isinstance(val, dict) else str(val)[:80]}
    await scenario("api", s6())

    # S7 micro action latency (extract h1 on example.com) ---------------------
    async def s7():
        s = await engine.create_session()
        await s.navigate(SITES["static"])
        await texts(s, "h1")  # warm
        t0 = time.perf_counter()
        for _ in range(MICRO_ROUNDS):
            await texts(s, "h1")
        per = (time.perf_counter() - t0) * 1000 / MICRO_ROUNDS
        await s.close()
        return True, {"per_action_ms": round(per, 2)}
    await scenario("micro", s7())

    await session.close()
    await engine.__aexit__(None, None, None)

    rec.finish("results_anweb.json", cold_start_ms=round(cold_ms, 1),
               snapshots={k: {"chars": len(v), "head": v[:1200]} for k, v in snapshots.items()})


asyncio.run(main())
