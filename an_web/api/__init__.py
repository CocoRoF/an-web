"""
AI Tool API layer for AN-Web.

Exposes the engine as AI-callable tools with semantic targeting.
Low-level (CSS selector/XPath) and high-level (semantic query) both supported,
but AI-facing default is always high-level semantic targeting.

Modules:
    models      - Pydantic request/response models
    tool_schema - AI tool schema definitions (JSON Schema compatible)
    rpc         - Optional HTTP/RPC server interface
"""
