"""Data extraction action."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
from an_web.actions.base import Action

if TYPE_CHECKING:
    from an_web.core.session import Session
    from an_web.dom.semantics import ActionResult


class ExtractAction(Action):
    async def execute(
        self, session: Session,
        query: str = "",
        **kwargs: Any,
    ) -> ActionResult:
        from an_web.dom.semantics import ActionResult
        doc = getattr(session, "_current_document", None)
        if doc is None:
            return self._make_failure("extract", "no_document_loaded")

        results: list[dict[str, Any]] = []
        from an_web.dom.document import query_selector_all
        elements = query_selector_all(doc, query)
        for el in elements:
            results.append({
                "node_id": el.node_id,
                "tag": el.tag,
                "text": el.inner_text if hasattr(el, "inner_text") else el.text_content,
                "attributes": el.attributes,
            })

        return ActionResult(
            status="ok",
            action="extract",
            target=query,
            effects={"count": len(results), "results": results},
        )
