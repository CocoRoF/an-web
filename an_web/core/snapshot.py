"""Deterministic snapshot management for AN-Web."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Snapshot:
    snapshot_id: str
    timestamp: float
    url: str
    dom_hash: str
    semantic_data: dict[str, Any]
    network_state: dict[str, Any] = field(default_factory=dict)
    storage_state: dict[str, Any] = field(default_factory=dict)
    action_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "url": self.url,
            "dom_hash": self.dom_hash,
            "semantic_data": self.semantic_data,
            "network_state": self.network_state,
            "storage_state": self.storage_state,
            "action_log": self.action_log,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class SnapshotManager:
    """
    Manages deterministic page snapshots.

    Snapshots enable:
    - Replay: re-execute a sequence of actions from a known state
    - Diff:   compare semantic state before/after an action
    - Debug:  provide structured evidence for failure analysis
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}
        self._counter: int = 0

    def create(
        self,
        url: str,
        dom_content: str,
        semantic_data: dict[str, Any],
        network_state: dict[str, Any] | None = None,
        storage_state: dict[str, Any] | None = None,
        action_log: list[dict[str, Any]] | None = None,
    ) -> Snapshot:
        """Create and store a new snapshot."""
        dom_hash = hashlib.sha256(dom_content.encode()).hexdigest()[:16]
        self._counter += 1
        snapshot_id = f"snap-{int(time.time() * 1000)}-{self._counter}-{dom_hash}"

        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            timestamp=time.time(),
            url=url,
            dom_hash=dom_hash,
            semantic_data=semantic_data,
            network_state=network_state or {},
            storage_state=storage_state or {},
            action_log=action_log or [],
        )
        self._snapshots[snapshot_id] = snapshot
        return snapshot

    def get(self, snapshot_id: str) -> Snapshot | None:
        return self._snapshots.get(snapshot_id)

    def list_ids(self) -> list[str]:
        return list(self._snapshots.keys())

    def diff(self, id_a: str, id_b: str) -> dict[str, Any]:
        """Return semantic diff between two snapshots."""
        snap_a = self._snapshots.get(id_a)
        snap_b = self._snapshots.get(id_b)
        if not snap_a or not snap_b:
            return {"error": "snapshot not found"}
        return {
            "url_changed": snap_a.url != snap_b.url,
            "dom_changed": snap_a.dom_hash != snap_b.dom_hash,
            "from": id_a,
            "to": id_b,
        }
