"""
Semantic extraction engine — DOM → AI world model.

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
    from an_web.dom.nodes import Document, Element
    from an_web.dom.semantics import PageSemantics, SemanticNode


class SemanticExtractor:
    """
    Transforms DOM tree into AI-readable PageSemantics.

    Walk order and visibility logic:
    - Skip metadata tags (head, script, style, svg)
    - Skip invisible elements (display:none, hidden attr)
    - Skip pure whitespace text nodes
    - Compute ARIA role + accessible name (aria-labelledby, label[for], etc.)
    - Classify interactivity
    - Attach XPath + stable selector
    - Assign form_scope_id for form controls
    - Carry interaction_rank from element importance score
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
        from an_web.semantic.affordances import rank_primary_actions
        from an_web.semantic.page_type import classify_page_type

        # Build lookup helpers once before walking
        label_for_map = self._build_label_for_map(doc)
        id_element_map = self._build_id_element_map(doc)

        # Walk DOM → SemanticNode tree
        root_node = self._walk_document(doc, label_for_map, id_element_map)

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

    # ── Document-level helpers ────────────────────────────────────────────────

    def _build_label_for_map(self, doc: Document) -> dict[str, str]:
        """
        Build {input_id: label_text} map from label[for] elements.

        Scans all <label> elements in the document.
        """
        from an_web.dom.nodes import Element

        label_map: dict[str, str] = {}
        for node in doc.iter_descendants():
            if isinstance(node, Element) and node.tag == "label":
                for_id = node.get_attribute("for")
                if for_id:
                    text = node.text_content.strip()
                    if text:
                        label_map[for_id] = text
        return label_map

    def _build_id_element_map(self, doc: Document) -> dict[str, Element]:
        """
        Build {element_id: element} map for aria-labelledby resolution.

        Uses Document._id_map if populated, falls back to full scan.
        """
        from an_web.dom.nodes import Element

        # Use the Document's built-in id_map if available
        id_map = getattr(doc, "_id_map", {})
        if id_map:
            return id_map  # type: ignore[return-value]

        # Fallback: build from scratch
        result: dict[str, Element] = {}
        for node in doc.iter_descendants():
            if isinstance(node, Element):
                el_id = node.get_id()
                if el_id:
                    result[el_id] = node
        return result

    # ── Tree walking ──────────────────────────────────────────────────────────

    def _walk_document(
        self,
        doc: Document,
        label_for_map: dict[str, str],
        id_element_map: dict[str, Element],
    ) -> SemanticNode:
        from an_web.dom.semantics import SemanticNode

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
            self._walk_node(
                child, root, tag_counts,
                depth=0, parent_xpath="/",
                label_for_map=label_for_map,
                id_element_map=id_element_map,
            )

        return root

    def _walk_node(
        self,
        node: Any,
        parent: SemanticNode,
        tag_counts: dict[str, int],
        depth: int,
        parent_xpath: str,
        label_for_map: dict[str, str],
        id_element_map: dict[str, Element],
    ) -> None:
        from an_web.dom.nodes import Element, TextNode
        from an_web.dom.semantics import SemanticNode
        from an_web.semantic.roles import get_affordances, infer_role, is_structural_role

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

            # Accessible name (aria-labelledby > aria-label > label[for] > ...)
            name = self._compute_accessible_name(
                node, label_for_map, id_element_map
            )

            # Prune structural nodes without content
            structural = is_structural_role(role)
            if self.prune and structural and not is_interactive and not name:
                # Still walk children (unrolled into parent)
                child_counts: dict[str, int] = {}
                for child in node.children:
                    self._walk_node(
                        child, parent, child_counts, depth + 1, xpath + "/",
                        label_for_map=label_for_map,
                        id_element_map=id_element_map,
                    )
                return

            if self.interactive_only and not is_interactive:
                return

            # Stable selector generation
            stable_selector = self._compute_stable_selector(node)

            # form_scope_id — walk parent chain for enclosing form
            form_scope_id = self._find_form_scope_id(node)

            # interaction_rank from element's importance_score
            interaction_rank = float(getattr(node, "importance_score", 0.0))

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
                interaction_rank=interaction_rank,
                form_scope_id=form_scope_id,
            )

            # options for select
            if node.tag == "select":
                sem_node.options = self._extract_select_options(node)

            parent.children.append(sem_node)

            # Walk children
            child_counts2: dict[str, int] = {}
            for child in node.children:
                self._walk_node(
                    child, sem_node, child_counts2, depth + 1, xpath + "/",
                    label_for_map=label_for_map,
                    id_element_map=id_element_map,
                )

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

    # ── Accessible name computation ───────────────────────────────────────────

    def _compute_accessible_name(
        self,
        element: Element,
        label_for_map: dict[str, str] | None = None,
        id_element_map: dict[str, Element] | None = None,
    ) -> str | None:
        """
        Compute accessible name following ARIA spec priority order:
          1. aria-labelledby (references another element by id)
          2. aria-label (inline string)
          3. label[for] lookup (for form controls with id)
          4. title attribute
          5. placeholder for inputs
          6. alt for images
          7. text content for buttons/links/labels
        """
        # 1. aria-labelledby — find referenced element(s) and concatenate their text
        labelledby = element.get_attribute("aria-labelledby")
        if labelledby and id_element_map:
            parts: list[str] = []
            for ref_id in labelledby.split():
                ref_el = id_element_map.get(ref_id)
                if ref_el is not None:
                    text = ref_el.text_content.strip()
                    if text:
                        parts.append(text)
            if parts:
                return " ".join(parts)

        # 2. aria-label
        aria_label = element.get_attribute("aria-label")
        if aria_label:
            return aria_label.strip()

        # 3. label[for] lookup — only for form controls that have an id
        el_id = element.get_id()
        if el_id and label_for_map:
            label_text = label_for_map.get(el_id)
            if label_text:
                return label_text

        # 4. title attribute
        title = element.get_attribute("title")
        if title:
            return title.strip()

        # 5. placeholder for inputs
        placeholder = element.get_attribute("placeholder")
        if placeholder:
            return placeholder.strip()

        # 6. alt for images
        if element.tag == "img":
            alt = element.get_attribute("alt")
            if alt:
                return alt.strip()

        # 7. text content for interactive elements only (buttons, links, labels).
        #    Headings are excluded: they are content containers, not interactive nodes.
        #    Adding heading text here would cause find_by_text() to match the heading
        #    *before* the interactive link it wraps (DFS pre-order), breaking click flows.
        if element.tag in ("button", "a", "label"):
            text = element.text_content.strip()
            if text and len(text) < 200:
                return text

        return None

    # ── Form scope ────────────────────────────────────────────────────────────

    def _find_form_scope_id(self, element: Element) -> str | None:
        """
        Walk parent chain to find the nearest enclosing <form>.

        Returns the form's node_id, or None if no form ancestor.
        Only relevant for form control elements.
        """
        _FORM_CONTROLS = frozenset({
            "input", "select", "textarea", "button",
        })
        if element.tag not in _FORM_CONTROLS:
            return None

        node = getattr(element, "parent", None)
        while node is not None:
            tag = getattr(node, "tag", "")
            if tag == "form":
                return getattr(node, "node_id", None)
            node = getattr(node, "parent", None)

        return None

    # ── Stable selector ───────────────────────────────────────────────────────

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

    # ── Select options ────────────────────────────────────────────────────────

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

    # ── Blocker detection ─────────────────────────────────────────────────────

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
