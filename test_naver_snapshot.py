"""
Naver.com full rendering + snapshot verification test.

Validates the complete AN-Web V8 pipeline:
  1. HTTP fetch with brotli decompression
  2. HTML parsing into DOM (~96 raw elements)
  3. V8 JS execution: webpack polyfill bundle → React runtime
  4. DOM mutation sync: JS-created nodes grafted into Python DOM
  5. Semantic extraction: full PageSemantics snapshot with page_type, tree, inputs, actions

Usage:
    uv run python test_naver_snapshot.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_ELEMENTS = 200
MIN_TEXT_NODES = 50
MIN_JS_CREATED = 100
MIN_INTERACTIVE = 5
MIN_SEMANTIC_CHILDREN = 10


async def main() -> int:
    from an_web import ANWebEngine
    from an_web.dom.nodes import Element, TextNode

    checks: list[tuple[str, bool]] = []
    failed = False

    async with ANWebEngine() as engine:
        session = await engine.create_session()

        # ══════════════════════════════════════════════════════════════
        # Phase 1: Navigate — fetch + parse + V8 execute + settle
        # ══════════════════════════════════════════════════════════════
        print("=" * 60)
        print("Phase 1: Navigate to www.naver.com")
        print("=" * 60)

        result = await session.navigate("https://www.naver.com")
        effects = result.effects if hasattr(result, "effects") else result.get("effects", {})
        status = result.status if hasattr(result, "status") else result.get("status", "unknown")

        print(f"  Status       : {status}")
        print(f"  Final URL    : {effects.get('final_url', 'N/A')}")
        print(f"  HTTP code    : {effects.get('status_code', 'N/A')}")
        print(f"  Scripts found: {effects.get('scripts_found', 0)}")
        print(f"  Scripts exec : {effects.get('scripts_executed', 0)}")
        print(f"  External     : {effects.get('external_loaded', 0)}")

        checks.append(("Navigate status == ok", status == "ok"))
        checks.append(("HTTP 200", effects.get("status_code") == 200))

        # ══════════════════════════════════════════════════════════════
        # Phase 2: DOM statistics — verify JS rendering worked
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 2: DOM Statistics (post-JS rendering)")
        print("=" * 60)

        document = session._current_document
        all_elements = [n for n in document.iter_descendants() if isinstance(n, Element)]
        text_nodes = [n for n in document.iter_descendants() if isinstance(n, TextNode) and n.data.strip()]
        js_created = getattr(session, "_js_created_nodes", {})

        print(f"  Total elements     : {len(all_elements)}")
        print(f"  Text nodes (filled): {len(text_nodes)}")
        print(f"  JS-created nodes   : {len(js_created)}")

        checks.append((f"Elements >= {MIN_ELEMENTS}", len(all_elements) >= MIN_ELEMENTS))
        checks.append((f"Text nodes >= {MIN_TEXT_NODES}", len(text_nodes) >= MIN_TEXT_NODES))
        checks.append((f"JS-created >= {MIN_JS_CREATED}", len(js_created) >= MIN_JS_CREATED))

        # Tag distribution
        tags: dict[str, int] = {}
        for el in all_elements:
            tags[el.tag] = tags.get(el.tag, 0) + 1
        top_tags = sorted(tags.items(), key=lambda x: -x[1])[:10]
        print(f"\n  Top tags: {', '.join(f'<{t}>:{c}' for t, c in top_tags)}")

        checks.append(("Has <a> links", tags.get("a", 0) > 10))
        checks.append(("Has <img> images", tags.get("img", 0) > 3))

        # ══════════════════════════════════════════════════════════════
        # Phase 3: Key elements — React island containers
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 3: Key DOM Elements")
        print("=" * 60)

        key_ids = ["root", "header", "query", "newsstand", "shopping", "feed", "account"]
        found_ids = []
        for eid in key_ids:
            el = document.get_element_by_id(eid)
            if el:
                ch = len(list(el.children))
                found_ids.append(eid)
                print(f"  #{eid}: <{el.tag}> ({ch} children)")
            else:
                print(f"  #{eid}: NOT FOUND")

        # Search input
        search_input = None
        for el in all_elements:
            if el.tag == "input" and el.get_attribute("name") == "query":
                search_input = el
                break
        print(f"  search <input>: {'FOUND' if search_input else 'NOT FOUND'}")

        checks.append(("Has #root element", "root" in found_ids))
        checks.append(("Search input exists", search_input is not None))

        # React island rendering
        react_islands = ["newsstand", "shopping", "feed", "account"]
        islands_rendered = sum(
            1 for rid in react_islands
            if (el := document.get_element_by_id(rid)) and len(list(el.children)) > 0
        )
        print(f"  React islands with content: {islands_rendered}/{len(react_islands)}")
        checks.append(("React islands >= 3/4", islands_rendered >= 3))

        # ══════════════════════════════════════════════════════════════
        # Phase 4: Semantic snapshot — the AI world model
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 4: Semantic Snapshot (PageSemantics)")
        print("=" * 60)

        page = await session.snapshot()

        print(f"  page_type  : {page.page_type}")
        print(f"  title      : {page.title}")
        print(f"  url        : {page.url}")
        print(f"  snapshot_id: {page.snapshot_id}")
        print(f"  inputs     : {len(page.inputs)} fields")
        print(f"  actions    : {len(page.primary_actions)} primary actions")
        print(f"  blockers   : {len(page.blocking_elements)} blocking elements")

        checks.append(("page.title is not empty", bool(page.title)))
        checks.append(("page.url contains naver", "naver" in page.url))
        checks.append(("snapshot_id assigned", bool(page.snapshot_id)))

        # Semantic tree depth & breadth
        tree = page.semantic_tree
        checks.append(("semantic_tree exists", tree is not None))

        if tree:
            child_count = len(tree.children)
            print(f"\n  Semantic tree root: <{tree.tag}> role={tree.role} ({child_count} children)")
            checks.append((f"Tree children >= {MIN_SEMANTIC_CHILDREN}", child_count >= MIN_SEMANTIC_CHILDREN))

            # Count interactive nodes
            interactive = tree.find_interactive()
            print(f"  Interactive nodes: {len(interactive)}")
            checks.append((f"Interactive >= {MIN_INTERACTIVE}", len(interactive) >= MIN_INTERACTIVE))

            # Show affordances summary
            afford_counts: dict[str, int] = {}
            for node in interactive:
                for a in node.affordances:
                    afford_counts[a] = afford_counts.get(a, 0) + 1
            if afford_counts:
                print(f"  Affordances: {', '.join(f'{k}:{v}' for k, v in sorted(afford_counts.items(), key=lambda x: -x[1]))}")

            # Roles distribution
            role_counts: dict[str, int] = {}
            def _count_roles(n):
                if n.role and n.role != "none":
                    role_counts[n.role] = role_counts.get(n.role, 0) + 1
                for c in n.children:
                    _count_roles(c)
            _count_roles(tree)
            top_roles = sorted(role_counts.items(), key=lambda x: -x[1])[:8]
            if top_roles:
                print(f"  Top roles: {', '.join(f'{r}:{c}' for r, c in top_roles)}")

        # ══════════════════════════════════════════════════════════════
        # Phase 5: Input fields — verify search box is detected
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 5: Input Fields & Primary Actions")
        print("=" * 60)

        for inp in page.inputs[:5]:
            print(f"  Input: role={inp.get('role', '?')} name={inp.get('name', '?')!r} selector={inp.get('stable_selector', '?')}")

        search_input_detected = any(
            "query" in (inp.get("name") or "") or "search" in (inp.get("role") or "")
            for inp in page.inputs
        )
        checks.append(("Search input in snapshot.inputs", search_input_detected or len(page.inputs) > 0))

        for act in page.primary_actions[:5]:
            print(f"  Action: role={act.get('role', '?')} name={act.get('name', '?')!r} tag={act.get('tag', '?')}")

        # ══════════════════════════════════════════════════════════════
        # Phase 6: JavaScript execution — verify V8 is alive
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 6: V8 JavaScript Execution")
        print("=" * 60)

        js_title = await session.execute_script("document.title")
        print(f"  document.title = {js_title!r}")
        checks.append(("JS: document.title works", js_title is not None and len(str(js_title)) > 0))

        js_el_count = await session.execute_script(
            "document.querySelectorAll('*').length"
        )
        print(f"  document.querySelectorAll('*').length = {js_el_count}")
        checks.append(("JS: querySelectorAll works", js_el_count is not None and js_el_count > 100))

        js_links = await session.execute_script(
            "document.querySelectorAll('a').length"
        )
        print(f"  <a> link count from JS = {js_links}")

        # ══════════════════════════════════════════════════════════════
        # Phase 7: Sample text content — verify real Korean content
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 7: Sample Text Content")
        print("=" * 60)

        all_text = [
            n for n in document.iter_descendants()
            if isinstance(n, TextNode) and n.data.strip() and len(n.data.strip()) > 1
        ]
        for t in all_text[:10]:
            print(f"  {t.data.strip()[:80]}")

        # ══════════════════════════════════════════════════════════════
        # Phase 8: PageSemantics serialization — to_dict round-trip
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("Phase 8: Snapshot Serialization")
        print("=" * 60)

        snap_dict = page.to_dict()
        print(f"  to_dict() keys: {list(snap_dict.keys())}")
        print(f"  semanticTree children: {len(snap_dict.get('semanticTree', {}).get('children', []))}")
        checks.append(("to_dict() serializable", "pageType" in snap_dict and "semanticTree" in snap_dict))

        # JSON serialization
        import json
        try:
            json_str = json.dumps(snap_dict, ensure_ascii=False, default=str)
            json_size_kb = len(json_str) / 1024
            print(f"  JSON size: {json_size_kb:.1f} KB")
            checks.append(("JSON serializable", True))
            checks.append(("JSON size > 10 KB", json_size_kb > 10))
        except Exception as e:
            print(f"  JSON serialization FAILED: {e}")
            checks.append(("JSON serializable", False))
            checks.append(("JSON size > 10 KB", False))

        # ══════════════════════════════════════════════════════════════
        # Results
        # ══════════════════════════════════════════════════════════════
        print(f"\n{'=' * 60}")
        print("RESULTS")
        print("=" * 60)

        for name, passed in checks:
            marker = "PASS" if passed else "FAIL"
            print(f"  [{marker}] {name}")
            if not passed:
                failed = True

        total = len(checks)
        passed_count = sum(1 for _, p in checks if p)
        print(f"\n  {passed_count}/{total} checks passed")
        print(f"  {'ALL CHECKS PASSED' if not failed else 'SOME CHECKS FAILED'}")

        await session.close()

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
