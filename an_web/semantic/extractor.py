"""
Semantic extraction engine — DOM → AI world model.

Corresponds to Lightpanda's SemanticTree.zig walk() function,
extended with page-level classification and action ranking.

Primary API:
    extractor = SemanticExtractor()
    page = extractor.extract_from_document(doc, url="https://example.com")
    # page.semantic_tree, page.primary_actions, page.inputs, etc.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.nodes import Document, Element, Node
    from an_web.dom.semantics import PageSemantics, SemanticNode


class SemanticExtractor:
    """
    Transforms DOM tree into AI-readable PageSemantics.

    Walk order and visibility logic mirrors Lightpanda SemanticTree.zig:
    - Skip metadata tags (head, script, style, svg)
    - Skip invisible elements (display:none, hidden attr)
    - Skip pure whitespace text nodes
    - Compute ARIA role + accessible name
    - Classify interactivity
    - Attach XPath + stable selector
    """

    def __init__(self, prune: bool = True, interactive_only: bool = False) -> None:
        self.prune = prune
        self.interactive_only = interactive_only

    def extract_from_document(
        self,
        doc: Document,
        url: str = "about:blank",
        snapshot_manager: Any = None,
    ) -> PageSemantics:
        """
        Synchronous extraction from a Document object.

        This is the primary entry point for all code that already has a parsed
        Document (e.g. NavigateAction, tests, offline processing).
        """
        from an_web.dom.semantics import PageSemantics
        from an_web.semantic.page_type import classify_page_type
        from an_web.semantic.affordances import rank_primary_actions

        # Walk DOM → SemanticNode tree
        root_node = self._walk_document(doc)

        title = getattr(doc, "title", "") or ""
        page_type = classify_page_type(root_node, title=title, url=url)

        # Collect interactive nodes
        interactive = root_node.find_interactive()
        interactive_dicts = [n.to_dict() for n in interactive]
        primary_actions = rank_primary_actions(interactive_dicts)[:5]

        # Collect input fields by ARIA role
        _INPUT_ROLES = frozenset({
            "textbox", "combobox", "searchbox", "spinbutton", "checkbox", "radio",
        })
        inputs = [n.to_dict() for n in interactive if n.role in _INPUT_ROLES]

        # Blocking elements (modals, banners)
        blocking = self._find_blockers(root_node)

        # Snapshot
        snap_id = f"snap-{uuid.uuid4().hex[:8]}"
        if snapshot_manager is not None:
            import html
            snap = snapshot_manager.create(
                url=url,
                dom_content=title,  # lightweight fingerprint
                semantic_data=root_node.to_dict(),
            )
            snap_id = snap.snapshot_id

        return PageSemantics(
            page_type=page_type,
            title=title,
            url=url,
            primary_actions=primary_actions,
            inputs=inputs,
            blocking_elements=blocking,
            semantic_tree=root_node,
            snapshot_id=snap_id,
        )

    async def extract(self, session: Session) -> PageSemantics:
        """Async extraction bound to a live Session."""
        from an_web.dom.semantics import PageSemantics, SemanticNode

        doc = getattr(session, "_current_document", None)
        url = getattr(session, "_current_url", "about:blank")

        if doc is None:
            empty_root = SemanticNode(
                node_id="root",
                tag="document",
                role="RootWebArea",
                name=None,
                value=None,
                xpath="/",
                is_interactive=False,
                visible=True,
            )
            return PageSemantics(
                page_type="empty",
                title="",
                url=url,
                primary_actions=[],
                inputs=[],
                blocking_elements=[],
                semantic_tree=empty_root,
                snapshot_id=f"snap-empty-{uuid.uuid4().hex[:8]}",
            )

        snap_mgr = getattr(session, "snapshots", None)
        return self.extract_from_document(doc, url=url, snapshot_manager=snap_mgr)

    def _walk_document(self, doc: Document) -> SemanticNode:
        from an_web.dom.semantics import SemanticNode
        from an_web.dom.nodes import Element, TextNode

        root = SemanticNode(
            node_id="__document__",
            tag="document",
            role="RootWebArea",
            name=getattr(doc, "title", None),
            value=None,
            xpath="/",
            is_interactive=False,
            visible=True,
        )

        tag_counts: dict[str, int] = {}
        for child in doc.children:
            self._walk_node(child, root, tag_counts, depth=0, parent_xpath="/")

        return root

    def _walk_node(
        self,
        node: Any,
        parent: SemanticNode,
        tag_counts: dict[str, int],
        depth: int,
        parent_xpath: str,
    ) -> None:
        from an_web.dom.nodes import Element, TextNode
        from an_web.dom.semantics import SemanticNode
        from an_web.semantic.roles import infer_role, get_affordances, is_structural_role

        MAX_DEPTH = 100
        if depth > MAX_DEPTH:
            return

        SKIP_TAGS = {"head", "script", "style", "svg", "meta", "link", "noscript"}

        if isinstance(node, Element):
            if node.tag in SKIP_TAGS:
                return
            if node.visibility_state == "none":
                return
            if node.is_hidden():
                return

            tag = node.tag
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            xpath = f"{parent_xpath}{tag}[{tag_counts[tag]}]"

            role = infer_role(node)
            affordances = get_affordances(role, node)
            is_interactive = bool(affordances) or node.is_interactive

            # Accessible name
            name = self._compute_accessible_name(node)

            # Prune structural nodes without content
            structural = is_structural_role(role)
            if self.prune and structural and not is_interactive and not name:
                # Still walk children (unrolled into parent)
                child_counts: dict[str, int] = {}
                for child in node.children:
                    self._walk_node(child, parent, child_counts, depth + 1, xpath + "/")
                return

            if self.interactive_only and not is_interactive:
                return

            # Stable selector generation
            stable_selector = self._compute_stable_selector(node)

            sem_node = SemanticNode(
                node_id=node.node_id,
                tag=node.tag,
                role=role,
                name=name,
                value=node.get_value() if hasattr(node, "get_value") else None,
                xpath=xpath,
                is_interactive=is_interactive,
                visible=node.visibility_state == "visible",
                attributes=node.attributes,
                affordances=affordances,
                stable_selector=stable_selector,
            )

            # options for select
            if node.tag == "select":
                sem_node.options = self._extract_select_options(node)

            parent.children.append(sem_node)

            # Walk children
            child_counts2: dict[str, int] = {}
            for child in node.children:
                self._walk_node(child, sem_node, child_counts2, depth + 1, xpath + "/")

        elif isinstance(node, TextNode):
            if self.interactive_only:
                return
            text = node.data.strip()
            if not text:
                return

            sem_node = SemanticNode(
                node_id=node.node_id,
                tag="text",
                role="StaticText",
                name=text,
                value=None,
                xpath=f"{parent_xpath}text()",
                is_interactive=False,
                visible=True,
            )
            parent.children.append(sem_node)

    def _compute_accessible_name(self, element: Element) -> str | None:
        """Compute accessible name following ARIA spec (simplified)."""
        # aria-label wins
        aria_label = element.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()

        # title attribute
        title = element.get_attribute("title")
        if title:
            return title.strip()

        # placeholder for inputs
        placeholder = element.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        # alt for images
        if element.tag == "img":
            alt = element.get_attribute("alt")
            if alt:
                return alt.strip()

        # text content for buttons/links
        if element.tag in ("button", "a", "label"):
            text = element.text_content.strip()
            if text and len(text) < 200:
                return text

        return None

    def _compute_stable_selector(self, element: Element) -> str | None:
        """Generate most reliable CSS selector for re-targeting."""
        el_id = element.get_id()
        if el_id:
            return f"#{el_id}"

        name = element.get_name()
        if name:
            return f"{element.tag}[name='{name}']"

        classes = element.get_class_list()
        if classes:
            return f"{element.tag}.{'.'.join(classes[:2])}"

        return None

    def _extract_select_options(self, element: Element) -> list[dict[str, Any]]:
        from an_web.dom.nodes import Element as El
        options: list[dict[str, Any]] = []
        for child in element.children:
            if isinstance(child, El) and child.tag == "option":
                options.append({
                    "value": child.get_attribute("value") or child.text_content,
                    "text": child.text_content.strip(),
                    "selected": "selected" in child.attributes,
                })
        return options

    def _find_blockers(self, tree: SemanticNode) -> list[dict[str, Any]]:
        """Find elements blocking interaction (modal, cookie banner)."""
        blockers: list[dict[str, Any]] = []
        BLOCKER_ROLES = {"dialog", "alertdialog"}
        BLOCKER_TEXTS = {"cookie", "consent", "accept", "privacy", "gdpr"}

        def _check(node: SemanticNode) -> None:
            if node.role in BLOCKER_ROLES:
                blockers.append({"node_id": node.node_id, "kind": node.role})
                return
            name_lower = (node.name or "").lower()
            if any(k in name_lower for k in BLOCKER_TEXTS) and node.is_interactive:
                blockers.append({"node_id": node.node_id, "kind": "cookie_banner"})
            for child in node.children:
                _check(child)

        _check(tree)
        return blockers
