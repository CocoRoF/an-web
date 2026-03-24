"""
Core DOM node model for AN-Web.

Implements a lightweight DOM tree sufficient for AI web automation.
Focuses on interactability and semantic enrichment over full W3C compliance.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class NodeType(Enum):
    ELEMENT = auto()
    TEXT = auto()
    DOCUMENT = auto()
    DOCUMENT_FRAGMENT = auto()
    COMMENT = auto()


@dataclass
class Node:
    """Base DOM node."""
    node_type: NodeType
    node_id: str
    parent: Node | None = field(default=None, repr=False)
    children: list[Node] = field(default_factory=list, repr=False)

    def append_child(self, child: Node) -> Node:
        child.parent = self
        self.children.append(child)
        return child

    def remove_child(self, child: Node) -> Node:
        child.parent = None
        self.children.remove(child)
        return child

    def iter_descendants(self) -> Iterator[Node]:
        """Depth-first iteration over all descendants."""
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    @property
    def text_content(self) -> str:
        """Concatenated text of all descendant text nodes."""
        parts: list[str] = []
        for node in self.iter_descendants():
            if isinstance(node, TextNode):
                parts.append(node.data)
        return "".join(parts)

    def is_element(self) -> bool:
        return self.node_type == NodeType.ELEMENT

    def is_text(self) -> bool:
        return self.node_type == NodeType.TEXT


@dataclass
class TextNode(Node):
    """DOM Text node."""
    data: str = ""

    def __init__(self, node_id: str, data: str) -> None:
        super().__init__(node_type=NodeType.TEXT, node_id=node_id)
        self.data = data

    @property
    def whole_text(self) -> str:
        return self.data


@dataclass
class Element(Node):
    """
    DOM Element node — the core unit of web interaction.

    AI-specific fields beyond standard DOM:
    - semantic_role: inferred ARIA role
    - is_interactive: whether AI can act on this element
    - visibility_state: CSS-computed visibility
    - affordances: list of possible actions ["click", "type", etc.]
    - stable_selector: most reliable selector for re-targeting
    - importance_score: AI-relevance score 0.0-1.0
    """

    tag: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    # AI-native fields
    semantic_role: str | None = None
    is_interactive: bool = False
    visibility_state: str = "visible"  # visible | hidden | none
    affordances: list[str] = field(default_factory=list)
    stable_selector: str | None = None
    importance_score: float = 0.5
    form_scope_id: str | None = None
    bbox_hint: tuple[int, int, int, int] | None = None  # x, y, w, h
    confidence: float = 1.0

    def __init__(self, node_id: str, tag: str, attributes: dict[str, str] | None = None) -> None:
        super().__init__(node_type=NodeType.ELEMENT, node_id=node_id)
        self.tag = tag.lower()
        self.attributes = attributes or {}
        # AI-native fields must be explicitly set here because the custom __init__
        # bypasses dataclass field initialization.
        self.semantic_role: str | None = None
        self.is_interactive: bool = False
        self.visibility_state: str = "visible"
        self.affordances: list[str] = []
        self.stable_selector: str | None = None
        self.importance_score: float = 0.5
        self.form_scope_id: str | None = None
        self.bbox_hint: tuple[int, int, int, int] | None = None
        self.confidence: float = 1.0

    def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes[name] = value

    def has_attribute(self, name: str) -> bool:
        return name in self.attributes

    def get_id(self) -> str | None:
        return self.attributes.get("id")

    def get_class_list(self) -> list[str]:
        cls = self.attributes.get("class", "")
        return [c for c in cls.split() if c]

    def get_name(self) -> str | None:
        return self.attributes.get("name")

    def get_value(self) -> str:
        return self.attributes.get("value", "")

    def is_disabled(self) -> bool:
        return "disabled" in self.attributes

    def is_hidden(self) -> bool:
        return "hidden" in self.attributes or self.attributes.get("type") == "hidden"

    @property
    def inner_text(self) -> str:
        """Visible text content (approximation without full layout)."""
        if self.visibility_state in ("hidden", "none"):
            return ""
        return self.text_content.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "tag": self.tag,
            "attributes": self.attributes,
            "semantic_role": self.semantic_role,
            "is_interactive": self.is_interactive,
            "visibility_state": self.visibility_state,
            "affordances": self.affordances,
            "stable_selector": self.stable_selector,
            "importance_score": self.importance_score,
            "inner_text": self.inner_text,
        }


@dataclass
class Document(Node):
    """DOM Document node — root of the page tree."""

    url: str = "about:blank"
    title: str = ""
    charset: str = "utf-8"
    _id_map: dict[str, Element] = field(default_factory=dict, repr=False)

    def __init__(self, url: str = "about:blank") -> None:
        super().__init__(node_type=NodeType.DOCUMENT, node_id="__document__")
        self.url = url
        self._id_map = {}

    def register_element(self, element: Element) -> None:
        """Register element in id-map for fast getElementById."""
        el_id = element.get_id()
        if el_id:
            self._id_map[el_id] = element

    def get_element_by_id(self, element_id: str) -> Element | None:
        return self._id_map.get(element_id)

    @property
    def body(self) -> Element | None:
        for node in self.iter_descendants():
            if isinstance(node, Element) and node.tag == "body":
                return node
        return None

    @property
    def head(self) -> Element | None:
        for node in self.iter_descendants():
            if isinstance(node, Element) and node.tag == "head":
                return node
        return None

    def iter_elements(self) -> Iterator[Element]:
        for node in self.iter_descendants():
            if isinstance(node, Element):
                yield node
