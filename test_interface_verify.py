"""Verify AN-Web simple interface works for README examples."""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")


async def test_basic_navigate_and_snapshot():
    """Most basic usage: navigate + snapshot."""
    from an_web import ANWebEngine

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        result = await session.navigate("https://httpbin.org/html")
        assert result["status"] == "ok", f"Navigate failed: {result}"

        snap = await session.snapshot()
        assert snap.url != "", f"No URL"
        assert snap.semantic_tree is not None, "No semantic tree"
        print(f"  [PASS] navigate + snapshot: url={snap.url!r}, page_type={snap.page_type!r}")
        await session.close()


async def test_act_dispatch():
    """act() dispatch — the unified AI tool interface."""
    from an_web import ANWebEngine

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        result = await session.act({"tool": "navigate", "url": "https://httpbin.org/html"})
        assert result["status"] == "ok", f"act(navigate) failed: {result}"
        print(f"  [PASS] act() dispatch: status={result['status']}")
        await session.close()


async def test_tool_interface():
    """ANWebToolInterface — typed helper methods."""
    from an_web import ANWebEngine
    from an_web.api import ANWebToolInterface

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        tools = ANWebToolInterface(session)

        result = await tools.navigate("https://httpbin.org/html")
        assert result["status"] == "ok"

        snap = await tools.snapshot()
        assert "semantic_tree" in snap or "title" in snap
        print(f"  [PASS] ANWebToolInterface: navigate + snapshot")
        await session.close()


async def test_tool_schemas():
    """Tool schemas for AI models."""
    from an_web.api.tool_schema import TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI, get_tool_names

    names = get_tool_names()
    assert "navigate" in names
    assert "click" in names
    assert "snapshot" in names
    assert len(TOOLS_FOR_CLAUDE) == len(names)
    assert len(TOOLS_FOR_OPENAI) == len(names)
    print(f"  [PASS] Tool schemas: {len(names)} tools defined: {', '.join(names)}")


async def test_extract():
    """Extract action — CSS selector extraction."""
    from an_web import ANWebEngine

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://httpbin.org/html")

        result = await session.act({"tool": "extract", "query": "h1"})
        assert result["status"] == "ok"
        results = result.get("effects", {}).get("results", [])
        print(f"  [PASS] extract: found {len(results)} results")
        await session.close()


async def test_eval_js():
    """eval_js — execute JavaScript."""
    from an_web import ANWebEngine

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        await session.navigate("https://httpbin.org/html")

        result = await session.act({"tool": "eval_js", "script": "document.title"})
        assert result["status"] == "ok"
        print(f"  [PASS] eval_js: result={result['effects'].get('result')!r}")
        await session.close()


async def test_policy():
    """Policy rules — domain restriction."""
    from an_web import ANWebEngine
    from an_web.policy.rules import PolicyRules

    policy = PolicyRules.sandboxed(allowed_domains=["httpbin.org"])

    async with ANWebEngine() as engine:
        session = await engine.create_session(policy=policy)
        result = await session.navigate("https://httpbin.org/html")
        assert result["status"] == "ok"
        print(f"  [PASS] policy sandboxed: allowed domain works")
        await session.close()


async def main():
    tests = [
        ("Basic navigate + snapshot", test_basic_navigate_and_snapshot),
        ("act() dispatch", test_act_dispatch),
        ("ANWebToolInterface", test_tool_interface),
        ("Tool schemas", test_tool_schemas),
        ("Extract action", test_extract),
        ("eval_js action", test_eval_js),
        ("Policy rules", test_policy),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            await fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} interface tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
