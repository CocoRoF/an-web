"""
SemanticNode model — the AI-native DOM representation.

Corresponds to Lightpanda's SemanticTree.zig NodeData struct,
but extended with AI-specific affordances and page-level context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticNode:
    """
    AI-enriched DOM node.

    This is what AN-Web exposes to AI agents instead of raw DOM.
    Fields map to Lightpanda's SemanticTree NodeData:
        node_id        → CDPNode.Id
        tag            → node_name
        role           → axn.getRole()
        name           → axn.getName()
        value          → input.getValue() / select.getValue()
        xpath          → XPath segment
        is_interactive → classifyInteractivity()
        visible        → checkVisibilityCached()
    """

    node_id: str
    tag: str
    role: str                              # ARIA role
    name: str | None                       # Accessible name
    value: str | None                      # Current value (inputs)
    xpath: str                             # XPath location
    is_interactive: bool                   # Can AI act on this?
    visible: bool                          # CSS-computed visibility
    attributes: dict[str, str] = field(default_factory=dict)
    children: list[SemanticNode] = field(default_factory=list)
    options: list[dict[str, Any]] | None = None  # select/datalist options
    affordances: list[str] = field(default_factory=list)  # ["click","type","select"]
    stable_selector: str | None = None
    confidence: float = 1.0
    interaction_rank: float = 0.0       # 0.0–1.0 from layout engine assessment
    form_scope_id: str | None = None    # node_id of enclosing <form>

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "nodeId": self.node_id,
            "nodeName": self.tag,
            "role": self.role,
            "xpath": self.xpath,
            "isInteractive": self.is_interactive,
            "visible": self.visible,
            "affordances": self.affordances,
            "confidence": self.confidence,
        }
        if self.name:
            d["name"] = self.name
        if self.value is not None:
            d["value"] = self.value
        if self.options:
            d["options"] = self.options
        if self.attributes:
            d["attributes"] = self.attributes
        if self.stable_selector:
            d["stableSelector"] = self.stable_selector
        if self.interaction_rank:
            d["interactionRank"] = round(self.interaction_rank, 3)
        if self.form_scope_id:
            d["formScopeId"] = self.form_scope_id
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    def find_by_role(self, role: str) -> list[SemanticNode]:
        """Find all descendants with the given ARIA role."""
        results: list[SemanticNode] = []
        if self.role == role:
            results.append(self)
        for child in self.children:
            results.extend(child.find_by_role(role))
        return results

    def find_interactive(self) -> list[SemanticNode]:
        """Find all interactive descendants."""
        results: list[SemanticNode] = []
        if self.is_interactive:
            results.append(self)
        for child in self.children:
            results.extend(child.find_interactive())
        return results

    def find_by_text(self, text: str, partial: bool = True) -> list[SemanticNode]:
        """Find nodes whose accessible name matches text."""
        results: list[SemanticNode] = []
        if self.name:
            match = (
                text.lower() in self.name.lower()
                if partial
                else self.name.lower() == text.lower()
            )
            if match:
                results.append(self)
        for child in self.children:
            results.extend(child.find_by_text(text, partial))
        return results


@dataclass
class ActionResult:
    """
    Structured result of an AI action.

    Corresponds to Lightpanda's action return structure.
    Includes recommended_next_actions for AI planning assistance.
    """

    status: str                            # "ok" | "failed" | "blocked"
    action: str
    target: str | None = None
    effects: dict[str, Any] = field(default_factory=dict)
    # effects keys:
    #   navigation: bool
    #   dom_mutations: int
    #   network_requests: int
    #   modal_opened: bool
    #   form_submitted: bool
    state_delta_id: str | None = None
    error: str | None = None
    error_details: dict[str, Any] = field(default_factory=dict)
    recommended_next_actions: list[dict[str, Any]] = field(default_factory=list)

    def is_ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "action": self.action,
        }
        if self.target:
            d["target"] = self.target
        if self.effects:
            d["effects"] = self.effects
        if self.state_delta_id:
            d["stateDeltaId"] = self.state_delta_id
        if self.error:
            d["error"] = self.error
            d["errorDetails"] = self.error_details
        if self.recommended_next_actions:
            d["recommendedNextActions"] = self.recommended_next_actions
        return d


@dataclass
class PageSemantics:
    """
    AI-facing representation of a page's current state.

    Corresponds to Lightpanda's SemanticTree JSON output,
    extended with page-level classification and action ranking.
    """

    page_type: str          # "login_form" | "search" | "listing" | "detail" | ...
    title: str
    url: str
    primary_actions: list[dict[str, Any]]   # Ranked action candidates
    inputs: list[dict[str, Any]]            # Input fields
    blocking_elements: list[dict[str, Any]] # Modal, cookie banner, etc.
    semantic_tree: SemanticNode             # Full semantic tree
    snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageType": self.page_type,
            "title": self.title,
            "url": self.url,
            "primaryActions": self.primary_actions,
            "inputs": self.inputs,
            "blockingElements": self.blocking_elements,
            "semanticTree": self.semantic_tree.to_dict(),
            "snapshotId": self.snapshot_id,
        }
