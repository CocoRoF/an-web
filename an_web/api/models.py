"""Pydantic request/response models for AN-Web AI Tool API."""
from __future__ import annotations

from typing import Any, Literal, Union
from pydantic import BaseModel, Field


# ─── Request models ───────────────────────────────────────────────────────────

class SemanticTarget(BaseModel):
    """High-level semantic target specification."""
    by: Literal["semantic", "role", "text", "node_id"]
    role: str | None = None
    text: str | None = None
    node_id: str | None = None
    name: str | None = None  # accessible name filter


class NavigateRequest(BaseModel):
    tool: Literal["navigate"] = "navigate"
    url: str


class ClickRequest(BaseModel):
    tool: Literal["click"] = "click"
    target: Union[str, SemanticTarget]


class TypeRequest(BaseModel):
    tool: Literal["type"] = "type"
    target: Union[str, SemanticTarget]
    text: str


class ClearRequest(BaseModel):
    tool: Literal["clear"] = "clear"
    target: Union[str, SemanticTarget]


class SelectRequest(BaseModel):
    tool: Literal["select"] = "select"
    target: Union[str, SemanticTarget]
    value: str


class SubmitRequest(BaseModel):
    tool: Literal["submit"] = "submit"
    target: Union[str, SemanticTarget]


class ExtractRequest(BaseModel):
    tool: Literal["extract"] = "extract"
    query: str  # CSS selector or semantic query


class SnapshotRequest(BaseModel):
    tool: Literal["snapshot"] = "snapshot"


class WaitForRequest(BaseModel):
    tool: Literal["wait_for"] = "wait_for"
    condition: Literal["network_idle", "dom_stable", "element_visible"]
    selector: str | None = None
    timeout_ms: int = 5000


class ScrollRequest(BaseModel):
    tool: Literal["scroll"] = "scroll"
    target: Union[str, SemanticTarget, None] = None
    delta_x: int = 0
    delta_y: int = 300


class EvalJSRequest(BaseModel):
    tool: Literal["eval_js"] = "eval_js"
    script: str


ToolRequest = Union[
    NavigateRequest, ClickRequest, TypeRequest, ClearRequest,
    SelectRequest, SubmitRequest, ExtractRequest, SnapshotRequest,
    WaitForRequest, ScrollRequest, EvalJSRequest,
]


# ─── Response models ──────────────────────────────────────────────────────────

class ActionEffects(BaseModel):
    navigation: bool = False
    final_url: str | None = None
    dom_mutations: int = 0
    network_requests: int = 0
    modal_opened: bool = False
    form_submitted: bool = False
    value_set: str | None = None
    count: int | None = None
    results: list[dict[str, Any]] | None = None


class ActionResponse(BaseModel):
    """Structured response for every AI tool call."""
    status: Literal["ok", "failed", "blocked"]
    action: str
    target: str | None = None
    effects: ActionEffects = Field(default_factory=ActionEffects)
    state_delta_id: str | None = None
    error: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)
    recommended_next_actions: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> ActionResponse:
        effects_data = result.get("effects", {})
        return cls(
            status=result.get("status", "ok"),
            action=result.get("action", ""),
            target=result.get("target"),
            effects=ActionEffects(**{
                k: v for k, v in effects_data.items()
                if k in ActionEffects.model_fields
            }),
            state_delta_id=result.get("state_delta_id") or result.get("stateDeltaId"),
            error=result.get("error"),
            error_details=result.get("error_details", {}),
            recommended_next_actions=result.get("recommended_next_actions", []),
        )


class SemanticNodeModel(BaseModel):
    """Pydantic-serializable SemanticNode."""
    node_id: str
    tag: str
    role: str
    name: str | None = None
    value: str | None = None
    xpath: str
    is_interactive: bool
    visible: bool
    attributes: dict[str, str] = Field(default_factory=dict)
    children: list[SemanticNodeModel] = Field(default_factory=list)
    options: list[dict[str, Any]] | None = None
    affordances: list[str] = Field(default_factory=list)
    stable_selector: str | None = None
    confidence: float = 1.0

    model_config = {"arbitrary_types_allowed": True}


class PageSemanticsResponse(BaseModel):
    """Full page semantics response for snapshot() tool."""
    page_type: str
    title: str
    url: str
    primary_actions: list[dict[str, Any]] = Field(default_factory=list)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    blocking_elements: list[dict[str, Any]] = Field(default_factory=list)
    semantic_tree: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str
