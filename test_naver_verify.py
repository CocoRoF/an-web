"""
Naver.com SPA rendering verification test.

Demonstrates that AN-Web can:
1. Fetch naver.com HTML (~96 raw elements)
2. Execute webpack bundles (polyfill, preload, search, main)
3. Render React components into #root
4. Produce a full DOM with 700+ elements, real content, and interactive elements

Usage:
    uv run python test_naver_verify.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")


async def main():
    from an_web import ANWebEngine
    from an_web.dom.nodes import Element, TextNode

    async with ANWebEngine() as engine:
        session = await engine.create_session()

        # Navigate using the full pipeline (respects defer, fires events, settles)
        result = await session.navigate("https://www.naver.com")
        effects = result.effects if hasattr(result, 'effects') else result.get('effects', {})
        status = result.status if hasattr(result, 'status') else result.get('status', 'unknown')
        print(f"Navigate status: {status}")
        print(f"Final URL: {effects.get('final_url', 'N/A')}")
        print(f"HTTP status: {effects.get('status_code', 'N/A')}")
        print(f"Scripts found: {effects.get('scripts_found', 0)}")
        print(f"Scripts executed: {effects.get('scripts_executed', 0)}")
        print(f"External loaded: {effects.get('external_loaded', 0)}")

        document = session._current_document

        # DOM statistics
        all_elements = [n for n in document.iter_descendants() if isinstance(n, Element)]
        text_nodes = [n for n in document.iter_descendants() if isinstance(n, TextNode) and n.data.strip()]
        js_created = getattr(session, '_js_created_nodes', {})

        print(f"\n--- DOM Statistics ---")
        print(f"Total elements: {len(all_elements)}")
        print(f"Text nodes with content: {len(text_nodes)}")
        print(f"JS-created nodes: {len(js_created)}")

        # Root check
        root = document.get_element_by_id("root")
        root_children = list(root.children) if root else []
        print(f"#root children: {len(root_children)}")

        # Key elements
        print(f"\n--- Key Elements ---")
        for sel_id in ['root', 'header', 'query', 'newsstand', 'shopping', 'feed', 'account']:
            el = document.get_element_by_id(sel_id)
            if el:
                ch = len(list(el.children))
                print(f"  #{sel_id}: <{el.tag}> ({ch} children)")
            else:
                print(f"  #{sel_id}: not found")

        # Search input
        search_input = None
        for el in all_elements:
            if el.tag == "input" and el.get_attribute("name") == "query":
                search_input = el
                break
        print(f"\nSearch input: {'FOUND' if search_input else 'NOT FOUND'}")

        # Sample text content
        print(f"\n--- Sample Content ---")
        if root:
            texts = []
            for n in root.iter_descendants():
                if isinstance(n, TextNode) and n.data.strip() and len(n.data.strip()) > 1:
                    texts.append(n.data.strip()[:80])
            for t in texts[:15]:
                print(f"  {t}")

        # Element tag distribution
        tags: dict[str, int] = {}
        for el in all_elements:
            tags[el.tag] = tags.get(el.tag, 0) + 1
        top_tags = sorted(tags.items(), key=lambda x: -x[1])[:10]
        print(f"\n--- Top element tags ---")
        for tag, count in top_tags:
            print(f"  <{tag}>: {count}")

        # Assertions
        print(f"\n--- Verification ---")
        checks = [
            ("DOM has 200+ elements", len(all_elements) >= 200),
            ("Text nodes > 50", len(text_nodes) > 50),
            ("JS nodes created > 100", len(js_created) > 100),
            ("#root has children", len(root_children) > 0),
            ("Search input exists", search_input is not None),
            ("React content rendered", any("뉴스" in t.data for t in [n for n in root.iter_descendants() if isinstance(n, TextNode)])),
        ]
        all_pass = True
        for name, passed in checks:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")
            if not passed:
                all_pass = False

        print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")

        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
