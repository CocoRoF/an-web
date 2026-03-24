"""Deterministic replay from saved traces."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.core.session import Session


class ReplayEngine:
    """Replays a sequence of actions from a saved artifact log."""

    async def replay(
        self,
        session: Session,
        action_log: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for entry in action_log:
            tool = entry.get("tool") or entry.get("action", "")
            params = {k: v for k, v in entry.items() if k not in ("tool", "action")}
            result = await session.act({"tool": tool, **params})
            results.append(result if isinstance(result, dict) else {"result": str(result)})
        return results
