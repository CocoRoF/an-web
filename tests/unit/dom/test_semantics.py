"""Unit tests for core data models: SemanticNode, ActionResult, PageSemantics."""
from __future__ import annotations

import pytest
from an_web.dom.semantics import ActionResult, PageSemantics, SemanticNode


# ─── SemanticNode ─────────────────────────────────────────────────────────────

class TestSemanticNode:
    def _make_node(self, **kwargs) -> SemanticNode:
        defaults = dict(
            node_id="n1",
            tag="button",
            role="button",
            name="Submit",
            value=None,
            xpath="/body[1]/form[1]/button[1]",
            is_interactive=True,
            visible=True,
            attributes={"class": "btn-primary"},
            affordances=["click"],
        )
        defaults.update(kwargs)
        return SemanticNode(**defaults)

    def test_basic_creation(self):
        node = self._make_node()
        assert node.node_id == "n1"
        assert node.tag == "button"
        assert node.role == "button"
        assert node.name == "Submit"
        assert node.is_interactive is True
        assert node.visible is True

    def test_to_dict_contains_required_keys(self):
        node = self._make_node()
        d = node.to_dict()
        assert d["nodeId"] == "n1"
        assert d["nodeName"] == "button"
        assert d["role"] == "button"
        assert d["name"] == "Submit"
        assert d["isInteractive"] is True
        assert d["visible"] is True
        assert d["xpath"] == "/body[1]/form[1]/button[1]"

    def test_to_dict_omits_none_fields(self):
        node = self._make_node(name=None, value=None)
        d = node.to_dict()
        assert "name" not in d
        assert "value" not in d

    def test_to_dict_includes_children(self):
        parent = self._make_node(node_id="p1")
        child = self._make_node(node_id="c1", name="Child")
        parent.children.append(child)
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["nodeId"] == "c1"

    def test_to_dict_includes_options_for_select(self):
        node = self._make_node(
            tag="select", role="combobox",
            options=[{"value": "a", "text": "Option A", "selected": False}],
        )
        d = node.to_dict()
        assert "options" in d
        assert d["options"][0]["value"] == "a"

    def test_find_by_role_self(self):
        node = self._make_node(role="button")
        results = node.find_by_role("button")
        assert len(results) == 1
        assert results[0] is node

    def test_find_by_role_descendant(self):
        root = self._make_node(node_id="root", role="form", is_interactive=False)
        btn = self._make_node(node_id="btn", role="button")
        link = self._make_node(node_id="lnk", role="link", name="Click")
        root.children.extend([btn, link])
        buttons = root.find_by_role("button")
        assert len(buttons) == 1
        assert buttons[0].node_id == "btn"

    def test_find_by_role_no_match(self):
        node = self._make_node(role="button")
        assert node.find_by_role("textbox") == []

    def test_find_interactive(self):
        root = self._make_node(node_id="root", role="generic", is_interactive=False)
        btn = self._make_node(node_id="btn", is_interactive=True)
        txt = self._make_node(node_id="txt", role="StaticText", is_interactive=False)
        root.children.extend([btn, txt])
        interactive = root.find_interactive()
        assert any(n.node_id == "btn" for n in interactive)
        assert all(n.node_id != "txt" for n in interactive)

    def test_find_by_text_partial(self):
        node = self._make_node(name="Log In to Account")
        results = node.find_by_text("log in")
        assert len(results) == 1

    def test_find_by_text_exact(self):
        node = self._make_node(name="Log In")
        assert node.find_by_text("Log In", partial=False) != []
        assert node.find_by_text("Log", partial=False) == []

    def test_find_by_text_descendant(self):
        root = self._make_node(node_id="root", name=None, role="generic", is_interactive=False)
        child = self._make_node(node_id="c1", name="Continue")
        root.children.append(child)
        results = root.find_by_text("continue")
        assert len(results) == 1
        assert results[0].node_id == "c1"

    def test_children_default_empty(self):
        node = self._make_node()
        assert node.children == []

    def test_affordances_default_empty(self):
        node = SemanticNode(
            node_id="x", tag="div", role="generic", name=None, value=None,
            xpath="/div[1]", is_interactive=False, visible=True,
        )
        assert node.affordances == []

    def test_confidence_default(self):
        node = self._make_node()
        assert node.confidence == 1.0

    def test_stable_selector_optional(self):
        node = self._make_node(stable_selector="#submit-btn")
        d = node.to_dict()
        assert d["stableSelector"] == "#submit-btn"

    def test_nested_find(self):
        """Deep nested find_by_role works correctly."""
        root = self._make_node(node_id="root", role="generic", is_interactive=False)
        level1 = self._make_node(node_id="l1", role="form", is_interactive=False)
        level2 = self._make_node(node_id="l2", role="button")
        level1.children.append(level2)
        root.children.append(level1)
        found = root.find_by_role("button")
        assert found[0].node_id == "l2"


# ─── ActionResult ────────────────────────────────────────────────────────────

class TestActionResult:
    def test_ok_result(self):
        result = ActionResult(
            status="ok",
            action="click",
            target="btn-1",
            effects={"navigation": False, "dom_mutations": 3},
        )
        assert result.is_ok() is True
        assert result.action == "click"
        assert result.target == "btn-1"

    def test_failed_result(self):
        result = ActionResult(
            status="failed",
            action="click",
            error="target_not_found",
        )
        assert result.is_ok() is False
        assert result.error == "target_not_found"

    def test_blocked_result(self):
        result = ActionResult(status="blocked", action="navigate")
        assert result.status == "blocked"
        assert result.is_ok() is False

    def test_to_dict_ok(self):
        result = ActionResult(
            status="ok",
            action="navigate",
            target="https://example.com",
            effects={"navigation": True, "final_url": "https://example.com"},
            state_delta_id="snap-abc123",
        )
        d = result.to_dict()
        assert d["status"] == "ok"
        assert d["action"] == "navigate"
        assert d["target"] == "https://example.com"
        assert d["effects"]["navigation"] is True
        assert d["stateDeltaId"] == "snap-abc123"

    def test_to_dict_failed_includes_error(self):
        result = ActionResult(
            status="failed",
            action="click",
            error="target_occluded",
            error_details={"occluded_by": "modal-1"},
            recommended_next_actions=[{"tool": "click", "target": "modal-close"}],
        )
        d = result.to_dict()
        assert d["error"] == "target_occluded"
        assert d["errorDetails"]["occluded_by"] == "modal-1"
        assert len(d["recommendedNextActions"]) == 1

    def test_to_dict_omits_empty_target(self):
        result = ActionResult(status="ok", action="scroll")
        d = result.to_dict()
        assert "target" not in d

    def test_to_dict_omits_empty_error(self):
        result = ActionResult(status="ok", action="navigate")
        d = result.to_dict()
        assert "error" not in d

    def test_effects_default_empty(self):
        result = ActionResult(status="ok", action="click")
        assert result.effects == {}

    def test_recommended_next_actions_default_empty(self):
        result = ActionResult(status="failed", action="click", error="not_found")
        assert result.recommended_next_actions == []

    def test_with_recommended_actions(self):
        result = ActionResult(
            status="failed",
            action="click",
            error="target_occluded",
            recommended_next_actions=[
                {"tool": "click", "target": {"by": "role", "role": "button", "text": "Accept"}},
            ],
        )
        d = result.to_dict()
        assert d["recommendedNextActions"][0]["tool"] == "click"


# ─── PageSemantics ────────────────────────────────────────────────────────────

class TestPageSemantics:
    def _make_tree(self) -> SemanticNode:
        root = SemanticNode(
            node_id="root", tag="document", role="RootWebArea",
            name="Test Page", value=None, xpath="/",
            is_interactive=False, visible=True,
        )
        form = SemanticNode(
            node_id="form1", tag="form", role="form", name=None,
            value=None, xpath="/form[1]", is_interactive=False, visible=True,
        )
        email = SemanticNode(
            node_id="inp1", tag="input", role="textbox", name="Email",
            value="", xpath="/form[1]/input[1]", is_interactive=True,
            visible=True, affordances=["type", "clear"],
        )
        submit = SemanticNode(
            node_id="btn1", tag="button", role="button", name="Log In",
            value=None, xpath="/form[1]/button[1]", is_interactive=True,
            visible=True, affordances=["click"],
        )
        form.children.extend([email, submit])
        root.children.append(form)
        return root

    def test_basic_creation(self):
        tree = self._make_tree()
        ps = PageSemantics(
            page_type="login_form",
            title="Login",
            url="https://example.com/login",
            primary_actions=[{"node_id": "btn1", "role": "button", "name": "Log In"}],
            inputs=[{"node_id": "inp1", "role": "textbox", "name": "Email"}],
            blocking_elements=[],
            semantic_tree=tree,
            snapshot_id="snap-001",
        )
        assert ps.page_type == "login_form"
        assert ps.title == "Login"
        assert ps.snapshot_id == "snap-001"

    def test_to_dict(self):
        tree = self._make_tree()
        ps = PageSemantics(
            page_type="login_form",
            title="Login",
            url="https://example.com/login",
            primary_actions=[{"node_id": "btn1"}],
            inputs=[{"node_id": "inp1"}],
            blocking_elements=[],
            semantic_tree=tree,
            snapshot_id="snap-001",
        )
        d = ps.to_dict()
        assert d["pageType"] == "login_form"
        assert d["title"] == "Login"
        assert d["url"] == "https://example.com/login"
        assert d["snapshotId"] == "snap-001"
        assert "semanticTree" in d
        assert d["semanticTree"]["nodeId"] == "root"

    def test_to_dict_includes_all_sections(self):
        tree = self._make_tree()
        ps = PageSemantics(
            page_type="search",
            title="Search",
            url="https://example.com/search",
            primary_actions=[{"node_id": "btn1", "action": "click"}],
            inputs=[{"node_id": "inp1", "kind": "searchbox"}],
            blocking_elements=[{"node_id": "modal1", "kind": "cookie_banner"}],
            semantic_tree=tree,
            snapshot_id="snap-002",
        )
        d = ps.to_dict()
        assert len(d["primaryActions"]) == 1
        assert len(d["inputs"]) == 1
        assert len(d["blockingElements"]) == 1

    def test_semantic_tree_find_interactive(self):
        tree = self._make_tree()
        ps = PageSemantics(
            page_type="login_form", title="", url="",
            primary_actions=[], inputs=[], blocking_elements=[],
            semantic_tree=tree, snapshot_id="",
        )
        interactive = ps.semantic_tree.find_interactive()
        ids = [n.node_id for n in interactive]
        assert "inp1" in ids
        assert "btn1" in ids
        assert "root" not in ids

    def test_page_type_variations(self):
        """PageSemantics accepts any page_type string."""
        for pt in ["login_form", "search", "listing", "detail", "checkout", "error", "generic"]:
            tree = self._make_tree()
            ps = PageSemantics(
                page_type=pt, title="", url="",
                primary_actions=[], inputs=[], blocking_elements=[],
                semantic_tree=tree, snapshot_id=f"snap-{pt}",
            )
            assert ps.page_type == pt

    def test_empty_blocking_elements(self):
        tree = self._make_tree()
        ps = PageSemantics(
            page_type="generic", title="", url="",
            primary_actions=[], inputs=[], blocking_elements=[],
            semantic_tree=tree, snapshot_id="snap-x",
        )
        assert ps.blocking_elements == []
        d = ps.to_dict()
        assert d["blockingElements"] == []
