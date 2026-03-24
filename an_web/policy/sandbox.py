"""Execution sandbox per session."""
from __future__ import annotations


class Sandbox:
    """Per-session execution isolation context."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._storage: dict[str, str] = {}

    def get_storage(self, key: str) -> str | None:
        return self._storage.get(key)

    def set_storage(self, key: str, value: str) -> None:
        self._storage[key] = value

    def clear_storage(self) -> None:
        self._storage.clear()
