"""Unit tests for SnapshotManager."""
from __future__ import annotations

import pytest
from an_web.core.snapshot import Snapshot, SnapshotManager


class TestSnapshotManager:
    @pytest.fixture
    def manager(self):
        return SnapshotManager()

    def test_create_returns_snapshot(self, manager):
        snap = manager.create(
            url="https://example.com",
            dom_content="<html></html>",
            semantic_data={"page_type": "generic"},
        )
        assert snap.url == "https://example.com"
        assert snap.snapshot_id.startswith("snap-")
        assert len(snap.dom_hash) == 16

    def test_get_by_id(self, manager):
        snap = manager.create(url="https://a.com", dom_content="<p>", semantic_data={})
        retrieved = manager.get(snap.snapshot_id)
        assert retrieved is snap

    def test_get_missing_id(self, manager):
        assert manager.get("nonexistent") is None

    def test_list_ids(self, manager):
        s1 = manager.create(url="", dom_content="a", semantic_data={})
        s2 = manager.create(url="", dom_content="b", semantic_data={})
        ids = manager.list_ids()
        assert s1.snapshot_id in ids
        assert s2.snapshot_id in ids

    def test_diff_url_changed(self, manager):
        s1 = manager.create(url="https://a.com", dom_content="<p>", semantic_data={})
        s2 = manager.create(url="https://b.com", dom_content="<p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["url_changed"] is True

    def test_diff_dom_changed(self, manager):
        s1 = manager.create(url="", dom_content="<p>hello</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>world</p>", semantic_data={})
        diff = manager.diff(s1.snapshot_id, s2.snapshot_id)
        assert diff["dom_changed"] is True

    def test_diff_missing_snapshot(self, manager):
        diff = manager.diff("bad-id-1", "bad-id-2")
        assert "error" in diff

    def test_snapshot_to_json(self, manager):
        snap = manager.create(url="https://x.com", dom_content="<html>", semantic_data={})
        json_str = snap.to_json()
        assert "snap-" in json_str
        assert "https://x.com" in json_str

    def test_same_content_same_dom_hash(self, manager):
        s1 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>same</p>", semantic_data={})
        assert s1.dom_hash == s2.dom_hash

    def test_different_content_different_dom_hash(self, manager):
        s1 = manager.create(url="", dom_content="<p>a</p>", semantic_data={})
        s2 = manager.create(url="", dom_content="<p>b</p>", semantic_data={})
        assert s1.dom_hash != s2.dom_hash
