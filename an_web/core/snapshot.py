"""
Deterministic snapshot management for AN-Web.

A Snapshot captures the complete observable state of a browser session at
a point in time: DOM hash, semantic data, network state, storage state,
and action log.  Snapshots enable:

- **Replay**: re-execute an action sequence from a known state.
- **Diff**: compare semantic state before/after an action.
- **Debug**: provide structured, reproducible evidence for failure analysis.
- **Audit**: full action provenance trail per page load.

Usage::

    manager = SnapshotManager()

    # Take snapshot after navigate
    snap = manager.create(
        url=session.current_url,
        dom_content=html,
        semantic_data=page.to_dict(),
        storage_state=session.storage_state(),
    )

    # Record actions against the snapshot
    manager.append_action(snap.snapshot_id, {"tool": "click", "target": "#btn"})

    # Compare two snapshots
    diff = manager.diff(before_id, after_id)

    # Get the most recent snapshot
    latest = manager.latest()

    # Prune old snapshots to cap memory
    manager.prune(max_count=50)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Snapshot:
    """
    Immutable record of browser session state at a point in time.

    The ``snapshot_id`` is deterministic: same content always produces
    the same ID (timestamp-ms + counter + dom-hash prefix).
    """

    snapshot_id: str
    timestamp: float           # UNIX timestamp of snapshot creation
    url: str                   # Page URL at snapshot time
    dom_hash: str              # SHA-256[:16] of raw HTML content
    semantic_data: dict[str, Any]        # PageSemantics.to_dict()
    network_state: dict[str, Any] = field(default_factory=dict)
    storage_state: dict[str, Any] = field(default_factory=dict)
    action_log: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

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

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def action_count(self) -> int:
        """Number of actions recorded against this snapshot."""
        return len(self.action_log)

    def has_semantic(self, key: str) -> bool:
        """Return True if ``semantic_data`` contains ``key``."""
        return key in self.semantic_data

    def __repr__(self) -> str:
        return (
            f"Snapshot("
            f"id={self.snapshot_id[:20]}..., "
            f"url={self.url!r}, "
            f"actions={self.action_count}"
            f")"
        )


class SnapshotManager:
    """
    Ordered store of Snapshot objects with diff and prune capabilities.

    Snapshots are stored in creation order.  IDs are unique within a
    manager but not globally (UUIDs are not used intentionally — the
    deterministic ID scheme enables content-addressable diffs).
    """

    def __init__(self) -> None:
        # Ordered store (insertion order preserved by dict in Python 3.7+)
        self._snapshots: dict[str, Snapshot] = {}
        self._counter: int = 0

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create(
        self,
        url: str,
        dom_content: str,
        semantic_data: dict[str, Any],
        network_state: dict[str, Any] | None = None,
        storage_state: dict[str, Any] | None = None,
        action_log: list[dict[str, Any]] | None = None,
    ) -> Snapshot:
        """
        Create and store a new snapshot.

        The ``snapshot_id`` is ``snap-<ts_ms>-<counter>-<dom_hash[:8]>``
        for human readability while remaining sortable by time.

        Args:
            url:           Page URL.
            dom_content:   Raw HTML string (used to compute ``dom_hash``).
            semantic_data: PageSemantics dict (from ``page.to_dict()``).
            network_state: Optional network trace / HAR data.
            storage_state: Optional cookie + localStorage + sessionStorage dump.
            action_log:    Optional initial action history.

        Returns:
            The created Snapshot (also stored internally).
        """
        dom_hash = hashlib.sha256(dom_content.encode("utf-8", errors="replace")).hexdigest()[:16]
        self._counter += 1
        ts_ms = int(time.time() * 1000)
        snapshot_id = f"snap-{ts_ms}-{self._counter}-{dom_hash[:8]}"

        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            timestamp=time.time(),
            url=url,
            dom_hash=dom_hash,
            semantic_data=semantic_data,
            network_state=network_state or {},
            storage_state=storage_state or {},
            action_log=list(action_log or []),
        )
        self._snapshots[snapshot_id] = snapshot
        return snapshot

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, snapshot_id: str) -> Snapshot | None:
        """Return a snapshot by ID, or ``None`` if not found."""
        return self._snapshots.get(snapshot_id)

    def list_ids(self) -> list[str]:
        """Return all snapshot IDs in creation order."""
        return list(self._snapshots.keys())

    def latest(self) -> Snapshot | None:
        """
        Return the most recently created snapshot.

        Returns ``None`` if no snapshots exist yet.
        """
        if not self._snapshots:
            return None
        return next(reversed(self._snapshots.values()))

    # ------------------------------------------------------------------
    # Action log
    # ------------------------------------------------------------------

    def append_action(
        self,
        snapshot_id: str,
        action: dict[str, Any],
    ) -> bool:
        """
        Append an action record to an existing snapshot's action log.

        Automatically stamps the action with ``"timestamp"`` if not present.

        Args:
            snapshot_id: ID of the snapshot to update.
            action:      Action dict (e.g. ``{"tool": "click", "target": "#btn"}``).

        Returns:
            ``True`` if the snapshot was found and updated, ``False`` otherwise.
        """
        snap = self._snapshots.get(snapshot_id)
        if snap is None:
            return False
        if "timestamp" not in action:
            action = {**action, "timestamp": time.time()}
        snap.action_log.append(action)
        return True

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def diff(self, id_a: str, id_b: str) -> dict[str, Any]:
        """
        Return a structural diff between two snapshots.

        Compares URL, DOM hash, and top-level semantic_data keys.

        Returns:
            A dict with boolean change flags and summary counts.
            On lookup failure returns ``{"error": "snapshot not found"}``.
        """
        snap_a = self._snapshots.get(id_a)
        snap_b = self._snapshots.get(id_b)
        if snap_a is None or snap_b is None:
            missing = []
            if snap_a is None:
                missing.append(id_a)
            if snap_b is None:
                missing.append(id_b)
            return {"error": "snapshot not found", "missing_ids": missing}

        # Semantic key-level diff
        sem_added: list[str] = []
        sem_removed: list[str] = []
        sem_changed: list[str] = []

        all_keys = set(snap_a.semantic_data) | set(snap_b.semantic_data)
        for k in all_keys:
            in_a = k in snap_a.semantic_data
            in_b = k in snap_b.semantic_data
            if in_a and not in_b:
                sem_removed.append(k)
            elif not in_a and in_b:
                sem_added.append(k)
            elif snap_a.semantic_data[k] != snap_b.semantic_data[k]:
                sem_changed.append(k)

        return {
            "from": id_a,
            "to": id_b,
            "url_changed": snap_a.url != snap_b.url,
            "dom_changed": snap_a.dom_hash != snap_b.dom_hash,
            "semantic_keys_added": sem_added,
            "semantic_keys_removed": sem_removed,
            "semantic_keys_changed": sem_changed,
            "action_count_delta": snap_b.action_count - snap_a.action_count,
            "elapsed_s": snap_b.timestamp - snap_a.timestamp,
        }

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def prune(self, max_count: int) -> int:
        """
        Remove oldest snapshots so that at most ``max_count`` remain.

        Args:
            max_count: Maximum number of snapshots to keep (>= 1).

        Returns:
            Number of snapshots removed.
        """
        max_count = max(1, max_count)
        excess = len(self._snapshots) - max_count
        if excess <= 0:
            return 0
        oldest_ids = list(self._snapshots.keys())[:excess]
        for sid in oldest_ids:
            del self._snapshots[sid]
        return excess

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of snapshots currently stored."""
        return len(self._snapshots)

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        # Always truthy — prevents `if session.snapshots:` from breaking
        # on an empty manager (empty != absent).
        return True

    def __len__(self) -> int:
        return self.count

    def __repr__(self) -> str:
        return f"SnapshotManager(count={self.count})"
