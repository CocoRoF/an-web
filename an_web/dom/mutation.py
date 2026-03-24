"""DOM mutation tracking — MutationObserver equivalent."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class MutationType(Enum):
    CHILD_LIST = auto()
    ATTRIBUTES = auto()
    CHARACTER_DATA = auto()


@dataclass
class MutationRecord:
    mutation_type: MutationType
    target_id: str
    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    attribute_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.mutation_type.name,
            "targetId": self.target_id,
            "addedNodes": self.added_nodes,
            "removedNodes": self.removed_nodes,
            "attributeName": self.attribute_name,
            "oldValue": self.old_value,
            "newValue": self.new_value,
        }


class MutationObserver:
    """Tracks DOM mutations during action execution."""

    def __init__(self) -> None:
        self._records: list[MutationRecord] = []

    def record(self, mutation: MutationRecord) -> None:
        self._records.append(mutation)

    def collect(self) -> list[MutationRecord]:
        """Return and clear recorded mutations."""
        records = self._records[:]
        self._records.clear()
        return records

    def count(self) -> int:
        return len(self._records)
