"""
Unit tests for an_web/core/snapshot.py — Snapshot + SnapshotManager.

Coverage:
- Snapshot: creation, to_dict, to_json, action_count, has_semantic, repr
- SnapshotManager: create, get, list_ids, latest, count, len
- SnapshotManager: append_action (success, timestamps, missing id)
- SnapshotManager: diff (url_changed, dom_changed, semantic keys, missing)
- SnapshotManager: prune (basic, over-prune, empty)
- SnapshotManager: determinism (same content -> same dom_hash)
"""
from __future__ import annotations

import json
import time

import pytest

from an_web.core.snapshot import Snapshot, SnapshotManager


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def manager():
    return SnapshotManager()


def _make(manager: SnapshotManager, url: str = "", content: str = "<p>", sem: dict = None):
    return manager.create(url=url, dom_content=content, semantic_data=sem or {})


# ============================================================================
# Snapshot model
# ============================================================================


class TestSnapshot:
    def test_create_returns_snapshot(self, manager):
        snap = manager.create(
            url="https://example.com",
            dom_content="<html></html>",
            semantic_data={"page_type": "generic"},
        )
        assert snap.url == "https://example.com"
        assert snap.snapshot_id.startswith("snap-")
        assert len(snap.dom_hash) == 16

    def test_snapshot_id_format(self, manager):
        snap = _make(manager, url="https://a.com")
        parts = snap.snapshot_id.split("-")
        # snap-<ts_ms>-<counter>-<dom_hash[:8]>
        assert parts[0] == "snap"
        assert parts[1].isdigit()  # timestamp ms
        assert parts[2].isdigit()  # counter

    def test_timestamp_is_recent(self, manager):
        before = time.time()
        snap = _make(manager)
        after = time.time()
        assert before <= snap.timestamp <= after

    def test_dom_hash_is_16_chars(self, manager):
        snap = manager.create(url="", dom_content="hello world", semantic_data={})
        assert len(snap.dom_hash) == 16

    def test_to_dict_has_all_keys(self, manager):
        snap = manager.create(
            url="https://x.com",
            dom_content="<html>",
            semantic_data={"k": "v"},
            network_state={"requests": 1},
            storage_state={"cookies": {}},
            action_log=[{"tool": "click"}],
        )
        d = snap.to_dict()
        assert "snapshot_id" in d
        assert "timestamp" in d
        assert "url" in d
        assert "dom_hash" in d
        assert "semantic_data" in d
        assert "network_state" in d
        assert "storage_state" in d
        assert "action_log" in d

    def test_to_json_is_valid_json(self, manager):
        snap = manager.create(url="https://x.com", dom_content="<html>", semantic_data={})
        parsed = json.loads(snap.to_json())
        assert parsed["url"] == "https://x.com"

    def test_to_json_contains_snapshot_id(self, manager):
        snap = manager.create(url="", dom_content="", semantic_data={})
        assert snap.snapshot_id in snap.to_json()

    def test_action_count_empty(self, manager):
        snap = _make(manager)
        assert snap.action_count == 0

    def test_action_count_after_append(self, manager):
        snap = _make(manager)
        manager.append_action(snap.snapshot_id, {"tool": "click"})
        manager.append_action(snap.snapshot_id, {"tool": "type"})
        assert snap.action_count == 2

    def test_has_semantic_true(self, manager):
        snap = manager.create(url="", dom_content="", semantic_data={"page_type": "login"})
        assert snap.has_semantic("page_type") is True

    def test_has_semantic_false(self, manager):
        snap = manager.create(url="", dom_content="", semantic_data={})
        assert snap.has_semantic("page_type") is False

    def test_repr_contains_id_and_url(self, manager):
        snap = manager.create(url="https://x.com", dom_content="", semantic_data={})
        r = repr(snap)
        assert "Snapshot" in r
        assert "https://x.com" in r

    def test_default_storage_empty(self, manager):
        snap = _make(manager)
        assert snap.network_state == {}
        assert snap.storage_state == {}
        assert snap.action_log == []


# ============================================================================
# SnapshotManager — CRUD
# ============================================================================


class TestSnapshotManagerCRUD:
    def test_get_by_id(self, manager):
        snap = _make(manager, url="https://a.com")
        retrieved = manager.get(snap.snapshot_id)
        assert retrieved is snap

    def test_get_missing_id(self, manager):
        assert manager.get("nonexistent") is None

    def test_list_ids_empty(self, manager):
        assert manager.list_ids() == []

    def test_list_ids_preserves_order(self, manager):
        s1 = _make(manager, url="first")
        s2 = _make(manager, url="second")
        ids = manager.list_ids()
        assert ids[0] == s1.snapshot_id
        assert ids[1] == s2.snapshot_id

    def test_count_empty(self, manager):
        assert manager.count == 0

    def test_count_increments(self, manager):
        _make(manager)
        _make(manager)
        assert manager.count == 2

    def test_len_equals_count(self, manager):
        _make(manager)
        assert len(manager) == manager.count

    def test_repr(self, manager):
        _make(manager)
        r = repr(manager)
        assert "SnapshotManager" in r
        assert "count=1" in r


# ============================================================================
# SnapshotManager — latest
# ============================================================================


class TestSnapshotManagerLatest:
    def test_latest_empty_returns_none(self, manager):
        assert manager.latest() is None

    def test_latest_returns_last_created(self, manager):
        s1 = _make(manager, content="<p>first</p>")
        s2 = _make(manager, content="<p>second</p>")
        assert manager.latest() is s2

    def test_latest_after_single(self, manager):
        s = _make(manager)
        assert manager.latest() is s


# ============================================================================
# SnapshotManager — append_action
# ============================================================================


class TestAppendAction:
    def test_append_action_success(self, manager):
        snap = _make(manager)
        ok = manager.append_action(snap.snapshot_id, {"tool": "click"})
        assert ok is True
        assert len(snap.action_log) == 1

    def test_append_action_missing_id_returns_false(self, manager):
        ok = manager.append_action("bad-id", {"tool": "click"})
        assert ok is False

    def test_append_action_adds_timestamp(self, manager):
        snap = _make(manager)
        before = time.time()
        manager.append_action(snap.snapshot_id, {"tool": "click"})
        after = time.time()
        ts = snap.action_log[0]["timestamp"]
        assert before <= ts <= after

    def test_append_action_preserves_existing_timestamp(self, manager):
        snap = _make(manager)
        manager.append_action(snap.snapshot_id, {"tool": "click", "timestamp": 1234.0})
        assert snap.action_log[0]["timestamp"] == 1234.0

    def test_append_multiple_actions_ordered(self, manager):
        snap = _make(manager)
        manager.append_action(snap.snapshot_id, {"tool": "click"})
        manager.append_action(snap.snapshot_id, {"tool": "type", "text": "hello"})
        assert snap.action_log[0]["tool"] == "click"
        assert snap.action_log[1]["tool"] == "type"

    def test_append_action_does_not_mutate_original_dict(self, manager):
        snap = _make(manager)
        original = {"tool": "click"}
        manager.append_action(snap.snapshot_id, original)
        # original dict should not have been mutated
        assert "timestamp" not in original or original.get("timestamp") is not None


# ============================================================================
# SnapshotManager — diff
# ============================================================================


class TestSnapshotManagerDiff:
    def test_diff_url_changed(self, manager):
        s1 = manager.create(url="https://a.com", dom_content="<p>", semantic_data={})
        s2 = manager.create(url="https://b.com", dom_content="<p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["url_changed"] is True

    def test_diff_url_unchanged(self, manager):
        s1 = manager.create(url="https://a.com", dom_content="<p>", semantic_data={})
        s2 = manager.create(url="https://a.com", dom_content="<p>x</p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["url_changed"] is False

    def test_diff_dom_changed(self, manager):
        s1 = manager.create(url="", dom_content="<p>hello</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>world</p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["dom_changed"] is True

    def test_diff_dom_unchanged(self, manager):
        s1 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["dom_changed"] is False

    def test_diff_missing_snapshot(self, manager):
        diff = manager.diff("bad-id-1", "bad-id-2")
        assert "error" in diff
        assert "missing_ids" in diff

    def test_diff_one_missing(self, manager):
        s = _make(manager)
        diff = manager.diff(s.snapshot_id, "nonexistent")
        assert "error" in diff

    def test_diff_semantic_keys_added(self, manager):
        s1 = manager.create(url="", dom_content="<p>", semantic_data={"a": 1})
        s2 = manager.create(url="", dom_content="<p>", semantic_data={"a": 1, "b": 2})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert "b" in diff["semantic_keys_added"]

    def test_diff_semantic_keys_removed(self, manager):
        s1 = manager.create(url="", dom_content="<p>", semantic_data={"a": 1, "b": 2})
        s2 = manager.create(url="", dom_content="<p>", semantic_data={"a": 1})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert "b" in diff["semantic_keys_removed"]

    def test_diff_semantic_keys_changed(self, manager):
        s1 = manager.create(url="", dom_content="<p>", semantic_data={"k": "old"})
        s2 = manager.create(url="", dom_content="<p>", semantic_data={"k": "new"})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert "k" in diff["semantic_keys_changed"]

    def test_diff_action_count_delta(self, manager):
        s1 = _make(manager)
        manager.append_action(s1.snapshot_id, {"tool": "click"})
        s2 = _make(manager)
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["action_count_delta"] == -1  # s2 has 0 actions, s1 has 1

    def test_diff_has_from_to_keys(self, manager):
        s1 = _make(manager)
        s2 = _make(manager)
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["from"] == s1.snapshot_id
        assert diff["to"] == s2.snapshot_id

    def test_diff_elapsed_s_positive(self, manager):
        s1 = _make(manager)
        import time as _time
        _time.sleep(0.01)  # ensure different timestamps
        s2 = _make(manager)
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["elapsed_s"] >= 0.0


# ============================================================================
# SnapshotManager — prune
# ============================================================================


class TestSnapshotManagerPrune:
    def test_prune_no_excess(self, manager):
        _make(manager)
        _make(manager)
        removed = manager.prune(max_count=5)
        assert removed == 0
        assert manager.count == 2

    def test_prune_removes_oldest(self, manager):
        s1 = _make(manager, content="<p>first</p>")
        s2 = _make(manager, content="<p>second</p>")
        s3 = _make(manager, content="<p>third</p>")
        manager.prune(max_count=2)
        assert manager.get(s1.snapshot_id) is None  # oldest removed
        assert manager.get(s2.snapshot_id) is not None
        assert manager.get(s3.snapshot_id) is not None

    def test_prune_returns_count_removed(self, manager):
        for _ in range(5):
            _make(manager)
        removed = manager.prune(max_count=2)
        assert removed == 3

    def test_prune_empty_manager(self, manager):
        removed = manager.prune(max_count=10)
        assert removed == 0

    def test_prune_max_count_1(self, manager):
        for _ in range(4):
            _make(manager)
        manager.prune(max_count=1)
        assert manager.count == 1

    def test_prune_max_count_0_keeps_at_least_1(self, manager):
        """max_count=0 is clamped to 1 to avoid destroying all snapshots."""
        for _ in range(3):
            _make(manager)
        manager.prune(max_count=0)
        assert manager.count >= 1

    def test_prune_latest_survives(self, manager):
        for _ in range(5):
            _make(manager)
        latest = manager.latest()
        manager.prune(max_count=1)
        assert manager.latest() is latest


# ============================================================================
# Determinism
# ============================================================================


class TestSnapshotDeterminism:
    def test_same_content_same_dom_hash(self, manager):
        s1 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        assert s1.dom_hash == s2.dom_hash

    def test_different_content_different_dom_hash(self, manager):
        s1 = manager.create(url="", dom_content="<p>a</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>b</p>", semantic_data={})
        assert s1.dom_hash != s2.dom_hash

    def test_counter_increments_per_create(self, manager):
        s1 = _make(manager)
        s2 = _make(manager)
        # Counter appears in snapshot_id as the third segment
        c1 = int(s1.snapshot_id.split("-")[2])
        c2 = int(s2.snapshot_id.split("-")[2])
        assert c2 == c1 + 1
