"""Pydantic request/response models for AN-Web AI Tool API."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ─── Target ───────────────────────────────────────────────────────────────────

class SemanticTarget(BaseModel):
    """High-level semantic target specification."""
    by: Literal["semantic", "role", "text", "node_id"]
    role: str | None = None
    text: str | None = None
    node_id: str | None = None
    name: str | None = None  # accessible name filter

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


# ─── Request models ───────────────────────────────────────────────────────────

class NavigateRequest(BaseModel):
    tool: Literal["navigate"] = "navigate"
    url: str

    @model_validator(mode="after")
    def _check_url(self) -> NavigateRequest:
        if not self.url:
            raise ValueError("url must not be empty")
        return self


class ClickRequest(BaseModel):
    tool: Literal["click"] = "click"
    target: str | SemanticTarget


class TypeRequest(BaseModel):
    tool: Literal["type"] = "type"
    target: str | SemanticTarget
    text: str
    append: bool = False


class ClearRequest(BaseModel):
    tool: Literal["clear"] = "clear"
    target: str | SemanticTarget


class SelectRequest(BaseModel):
    tool: Literal["select"] = "select"
    target: str | SemanticTarget
    value: str
    by_text: bool = False


class SubmitRequest(BaseModel):
    tool: Literal["submit"] = "submit"
    target: str | SemanticTarget


class ExtractRequest(BaseModel):
    tool: Literal["extract"] = "extract"
    query: str | dict = ""
    mode: Literal["css", "structured", "json", "html"] = "css"
    limit: int = 100


class SnapshotRequest(BaseModel):
    tool: Literal["snapshot"] = "snapshot"


class WaitForRequest(BaseModel):
    tool: Literal["wait_for"] = "wait_for"
    condition: Literal["network_idle", "dom_stable", "element_visible"]
    selector: str | None = None
    timeout_ms: int = 5000

    @model_validator(mode="after")
    def _check_element_visible(self) -> WaitForRequest:
        if self.condition == "element_visible" and not self.selector:
            raise ValueError("selector is required for condition='element_visible'")
        return self


class ScrollRequest(BaseModel):
    tool: Literal["scroll"] = "scroll"
    target: str | SemanticTarget | None = None
    delta_x: int = 0
    delta_y: int = 300


class EvalJSRequest(BaseModel):
    tool: Literal["eval_js"] = "eval_js"
    script: str

    @model_validator(mode="after")
    def _check_script(self) -> EvalJSRequest:
        if not self.script.strip():
            raise ValueError("script must not be empty")
        return self


ToolRequest = (
    NavigateRequest | ClickRequest | TypeRequest | ClearRequest
    | SelectRequest | SubmitRequest | ExtractRequest | SnapshotRequest
    | WaitForRequest | ScrollRequest | EvalJSRequest
)

# Mapping tool name → request model
TOOL_REQUEST_MAP: dict[str, type[BaseModel]] = {
    "navigate":  NavigateRequest,
    "click":     ClickRequest,
    "type":      TypeRequest,
    "clear":     ClearRequest,
    "select":    SelectRequest,
    "submit":    SubmitRequest,
    "extract":   ExtractRequest,
    "snapshot":  SnapshotRequest,
    "wait_for":  WaitForRequest,
    "scroll":    ScrollRequest,
    "eval_js":   EvalJSRequest,
}


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
    # JS eval
    result: Any = None
    raw_type: str | None = None
    available: bool | None = None
    # Wait-for
    condition: str | None = None
    satisfied: bool | None = None

    model_config = {"extra": "allow"}


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

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        return self.status in ("failed", "blocked")

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> ActionResponse:
        effects_data = result.get("effects", {})
        if not isinstance(effects_data, dict):
            effects_data = {}
        return cls(
            status=result.get("status", "ok"),
            action=result.get("action", ""),
            target=result.get("target"),
            effects=ActionEffects(**{
                k: v for k, v in effects_data.items()
            }),
            state_delta_id=result.get("state_delta_id") or result.get("stateDeltaId"),
            error=result.get("error"),
            error_details=result.get("error_details", {}),
            recommended_next_actions=result.get("recommended_next_actions", []),
        )

    def to_tool_result(self, tool_use_id: str | None = None) -> dict[str, Any]:
        """
        Format as an Anthropic tool_result block for use in the next message.

        ``content`` is a JSON string of the relevant response fields.
        ``is_error`` is True when status != 'ok'.
        """
        payload = self.model_dump(exclude_none=True)
        content = json.dumps(payload, ensure_ascii=False, default=str)
        result: dict[str, Any] = {
            "type": "tool_result",
            "content": content,
            "is_error": self.failed,
        }
        if tool_use_id:
            result["tool_use_id"] = tool_use_id
        return result


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

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> PageSemanticsResponse:
        return cls(
            page_type=result.get("page_type", "unknown"),
            title=result.get("title", ""),
            url=result.get("url", ""),
            primary_actions=result.get("primary_actions", []),
            inputs=result.get("inputs", []),
            blocking_elements=result.get("blocking_elements", []),
            semantic_tree=result.get("semantic_tree", {}),
            snapshot_id=result.get("snapshot_id", ""),
        )
