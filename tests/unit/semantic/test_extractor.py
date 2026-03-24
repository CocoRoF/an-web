"""Unit tests for SemanticExtractor."""
from __future__ import annotations

import pytest
from an_web.browser.parser import parse_html
from an_web.semantic.extractor import SemanticExtractor
from an_web.dom.semantics import SemanticNode, PageSemantics


# ─── Fixtures ─────────────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Login - MyApp</title></head>
<body>
  <form id="login-form" action="/login" method="post">
    <h1>Sign In</h1>
    <label for="email">Email</label>
    <input id="email" type="email" name="email" placeholder="you@example.com">
    <label for="pw">Password</label>
    <input id="pw" type="password" name="password" placeholder="Password">
    <button type="submit" class="btn-primary">Sign In</button>
    <a href="/forgot">Forgot password?</a>
  </form>
</body>
</html>
"""

SEARCH_HTML = """
<!DOCTYPE html>
<html>
<head><title>Search</title></head>
<body>
  <nav aria-label="Main navigation">
    <a href="/">Home</a>
    <a href="/about">About</a>
  </nav>
  <main>
    <form role="search" action="/search">
      <input type="search" name="q" placeholder="Search...">
      <button type="submit">Search</button>
    </form>
  </main>
</body>
</html>
"""

MODAL_HTML = """
<!DOCTYPE html>
<html><body>
  <div role="dialog" aria-label="Cookie consent">
    <p>We use cookies.</p>
    <button>Accept All</button>
    <button>Reject</button>
  </div>
  <main>
    <button>Main action</button>
  </main>
</body></html>
"""

SELECT_HTML = """
<!DOCTYPE html>
<html><body>
  <select name="country">
    <option value="us">United States</option>
    <option value="uk" selected>United Kingdom</option>
    <option value="de">Germany</option>
  </select>
</body></html>
"""


def make_extractor(**kw) -> SemanticExtractor:
    return SemanticExtractor(**kw)


# ─── PageSemantics basic structure ────────────────────────────────────────────

class TestExtractFromDocument:
    def test_returns_page_semantics(self):
        doc = parse_html(LOGIN_HTML, base_url="https://app.com/login")
        ps = make_extractor().extract_from_document(doc, url="https://app.com/login")
        assert isinstance(ps, PageSemantics)

    def test_title_extracted(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert ps.title == "Login - MyApp"

    def test_url_stored(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc, url="https://app.com/login")
        assert ps.url == "https://app.com/login"

    def test_snapshot_id_generated(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert ps.snapshot_id.startswith("snap-")

    def test_semantic_tree_is_semantic_node(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert isinstance(ps.semantic_tree, SemanticNode)
        assert ps.semantic_tree.role == "RootWebArea"


# ─── Page type classification ─────────────────────────────────────────────────

class TestPageTypeClassification:
    def test_login_page(self):
        doc = parse_html(LOGIN_HTML, base_url="https://app.com/login")
        ps = make_extractor().extract_from_document(doc, url="https://app.com/login")
        assert ps.page_type in ("login_form", "form")

    def test_search_page(self):
        doc = parse_html(SEARCH_HTML, base_url="https://app.com/search")
        ps = make_extractor().extract_from_document(doc, url="https://app.com/search")
        assert ps.page_type in ("search", "search_results", "generic")

    def test_empty_document(self):
        from an_web.dom.nodes import Document
        doc = Document(url="about:blank")
        ps = make_extractor().extract_from_document(doc, url="about:blank")
        assert isinstance(ps, PageSemantics)


# ─── Inputs detection ─────────────────────────────────────────────────────────

class TestInputsDetection:
    def test_login_inputs_found(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert len(ps.inputs) >= 2

    def test_input_has_role(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        roles = {inp["role"] for inp in ps.inputs}
        assert "textbox" in roles

    def test_search_input_detected(self):
        doc = parse_html(SEARCH_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert len(ps.inputs) >= 1


# ─── Primary actions detection ────────────────────────────────────────────────

class TestPrimaryActionsDetection:
    def test_submit_button_in_primary_actions(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert len(ps.primary_actions) >= 1

    def test_primary_actions_have_affordances(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        for action in ps.primary_actions:
            assert "affordances" in action


# ─── Blocking elements ────────────────────────────────────────────────────────

class TestBlockingElements:
    def test_dialog_detected_as_blocker(self):
        doc = parse_html(MODAL_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert len(ps.blocking_elements) >= 1
        kinds = {b["kind"] for b in ps.blocking_elements}
        assert "dialog" in kinds

    def test_no_blockers_on_clean_page(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        assert ps.blocking_elements == []


# ─── SemanticNode tree structure ──────────────────────────────────────────────

class TestSemanticNodeTree:
    def test_interactive_nodes_found(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        interactive = ps.semantic_tree.find_interactive()
        # email input, password input, submit button, forgot link
        assert len(interactive) >= 3

    def test_find_by_role_button(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        buttons = ps.semantic_tree.find_by_role("button")
        assert len(buttons) >= 1

    def test_find_by_role_link(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        links = ps.semantic_tree.find_by_role("link")
        assert len(links) >= 1  # forgot password link

    def test_find_by_text(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        nodes = ps.semantic_tree.find_by_text("Sign In")
        assert len(nodes) >= 1

    def test_node_has_xpath(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        interactive = ps.semantic_tree.find_interactive()
        for node in interactive:
            assert node.xpath and node.xpath != ""

    def test_node_has_stable_selector(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        # email input has id="email" → should have stable selector
        email_nodes = ps.semantic_tree.find_by_text("you@example.com")
        # check nodes with id
        interactive = ps.semantic_tree.find_interactive()
        selectors = [n.stable_selector for n in interactive if n.stable_selector]
        assert len(selectors) >= 1
        # email input should have #email
        assert any("#email" in (s or "") for s in selectors)


# ─── Accessible name computation ──────────────────────────────────────────────

class TestAccessibleName:
    def test_button_name_from_text(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        buttons = ps.semantic_tree.find_by_role("button")
        names = [b.name for b in buttons if b.name]
        assert any("Sign In" in n for n in names)

    def test_input_name_from_placeholder(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        textboxes = ps.semantic_tree.find_by_role("textbox")
        names = [t.name for t in textboxes if t.name]
        assert any("example.com" in (n or "") for n in names)

    def test_aria_label_wins(self):
        html = '<button aria-label="Close dialog">X</button>'
        doc = parse_html(html)
        ps = make_extractor().extract_from_document(doc)
        buttons = ps.semantic_tree.find_by_role("button")
        assert any(b.name == "Close dialog" for b in buttons)

    def test_link_name_from_text(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        links = ps.semantic_tree.find_by_role("link")
        names = [l.name for l in links if l.name]
        assert any("Forgot" in (n or "") for n in names)


# ─── Visibility filtering ─────────────────────────────────────────────────────

class TestVisibilityFiltering:
    def test_hidden_input_not_interactive(self):
        html = """
        <form>
            <input type="hidden" name="csrf" value="token">
            <input type="text" name="q">
        </form>
        """
        doc = parse_html(html)
        ps = make_extractor().extract_from_document(doc)
        # Only visible text input should appear as interactive
        interactive = ps.semantic_tree.find_interactive()
        types = [n.attributes.get("type", "text") for n in interactive if n.tag == "input"]
        assert "hidden" not in types

    def test_display_none_excluded(self):
        html = """
        <div style="display: none">
            <button id="hidden-btn">Hidden</button>
        </div>
        <button id="visible-btn">Visible</button>
        """
        doc = parse_html(html)
        ps = make_extractor().extract_from_document(doc)
        interactive = ps.semantic_tree.find_interactive()
        ids = [n.attributes.get("id", "") for n in interactive]
        assert "visible-btn" in ids
        assert "hidden-btn" not in ids


# ─── Select options ───────────────────────────────────────────────────────────

class TestSelectOptions:
    def test_options_extracted(self):
        doc = parse_html(SELECT_HTML)
        ps = make_extractor().extract_from_document(doc)
        combos = ps.semantic_tree.find_by_role("combobox")
        assert len(combos) >= 1
        select_node = combos[0]
        assert select_node.options is not None
        assert len(select_node.options) == 3

    def test_selected_option(self):
        doc = parse_html(SELECT_HTML)
        ps = make_extractor().extract_from_document(doc)
        combos = ps.semantic_tree.find_by_role("combobox")
        options = combos[0].options or []
        selected = [o for o in options if o.get("selected")]
        assert len(selected) == 1
        assert selected[0]["value"] == "uk"


# ─── interactive_only mode ────────────────────────────────────────────────────

class TestInteractiveOnlyMode:
    def test_non_interactive_pruned(self):
        doc = parse_html(LOGIN_HTML)
        ps = SemanticExtractor(interactive_only=True).extract_from_document(doc)
        # All nodes in tree should be interactive (no headings, text, etc.)
        def all_nodes(node: SemanticNode):
            yield node
            for c in node.children:
                yield from all_nodes(c)

        for node in all_nodes(ps.semantic_tree):
            if node.role == "RootWebArea":
                continue
            # StaticText and headings should be excluded in interactive_only mode
            assert node.is_interactive or node.role not in ("heading", "StaticText")


# ─── to_dict / serialization ──────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_complete(self):
        doc = parse_html(LOGIN_HTML, base_url="https://app.com")
        ps = make_extractor().extract_from_document(doc, url="https://app.com")
        d = ps.to_dict()
        # to_dict() uses camelCase keys
        assert d["pageType"] in ("login_form", "form")
        assert d["title"] == "Login - MyApp"
        assert d["url"] == "https://app.com"
        assert "primaryActions" in d
        assert "inputs" in d
        assert "semanticTree" in d
        assert "snapshotId" in d

    def test_semantic_node_to_dict(self):
        doc = parse_html(LOGIN_HTML)
        ps = make_extractor().extract_from_document(doc)
        interactive = ps.semantic_tree.find_interactive()
        for node in interactive:
            d = node.to_dict()
            # to_dict() uses camelCase keys
            assert "nodeId" in d
            assert "role" in d
            assert "isInteractive" in d
            assert "affordances" in d
