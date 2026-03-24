"""
AI Tool API layer for AN-Web.

Exposes the engine as AI-callable tools with semantic targeting.
Low-level (CSS selector/XPath) and high-level (semantic query) both supported,
but AI-facing default is always high-level semantic targeting.

Modules:
    models      - Pydantic request/response models
    tool_schema - AI tool schema definitions (JSON Schema / Claude / OpenAI)
    rpc         - Python Tool Interface (dispatch_tool, ANWebToolInterface)

Quick usage::

    from an_web.api import ANWebToolInterface, TOOLS_FOR_CLAUDE

    async with ANWebEngine() as engine:
        session = await engine.create_session()
        interface = ANWebToolInterface(session)
        await interface.navigate("https://example.com")
        page = await interface.snapshot()
"""
from an_web.api.models import (
    SemanticTarget,
    NavigateRequest, ClickRequest, TypeRequest, ClearRequest,
    SelectRequest, SubmitRequest, ExtractRequest, SnapshotRequest,
    WaitForRequest, ScrollRequest, EvalJSRequest,
    ToolRequest, TOOL_REQUEST_MAP,
    ActionEffects, ActionResponse,
    SemanticNodeModel, PageSemanticsResponse,
)
from an_web.api.tool_schema import (
    TOOLS, TOOLS_FOR_CLAUDE, TOOLS_FOR_OPENAI,
    get_tool, get_tool_names, get_schema,
)
from an_web.api.rpc import (
    dispatch_tool,
    ANWebToolInterface,
    _parse_tool_call,
    _normalize_target,
    _validate_request,
)

__all__ = [
    # models
    "SemanticTarget",
    "NavigateRequest", "ClickRequest", "TypeRequest", "ClearRequest",
    "SelectRequest", "SubmitRequest", "ExtractRequest", "SnapshotRequest",
    "WaitForRequest", "ScrollRequest", "EvalJSRequest",
    "ToolRequest", "TOOL_REQUEST_MAP",
    "ActionEffects", "ActionResponse",
    "SemanticNodeModel", "PageSemanticsResponse",
    # tool_schema
    "TOOLS", "TOOLS_FOR_CLAUDE", "TOOLS_FOR_OPENAI",
    "get_tool", "get_tool_names", "get_schema",
    # rpc
    "dispatch_tool", "ANWebToolInterface",
    "_parse_tool_call", "_normalize_target", "_validate_request",
]
