"""Unit tests for dom/mutation.py — MutationRecord and MutationObserver."""
from __future__ import annotations

import pytest
from an_web.dom.mutation import MutationObserver, MutationRecord, MutationType


class TestMutationType:
    def test_enum_members_exist(self):
        assert MutationType.CHILD_LIST
        assert MutationType.ATTRIBUTES
        assert MutationType.CHARACTER_DATA

    def test_enum_members_are_distinct(self):
        assert MutationType.CHILD_LIST != MutationType.ATTRIBUTES
        assert MutationType.ATTRIBUTES != MutationType.CHARACTER_DATA


class TestMutationRecord:
    def test_basic_construction(self):
        r = MutationRecord(mutation_type=MutationType.CHILD_LIST, target_id="el-1")
        assert r.mutation_type == MutationType.CHILD_LIST
        assert r.target_id == "el-1"
        assert r.added_nodes == []
        assert r.removed_nodes == []
        assert r.attribute_name is None
        assert r.old_value is None
        assert r.new_value is None

    def test_construction_with_all_fields(self):
        r = MutationRecord(
            mutation_type=MutationType.ATTRIBUTES,
            target_id="el-42",
            attribute_name="class",
            old_value="btn",
            new_value="btn active",
        )
        assert r.attribute_name == "class"
        assert r.old_value == "btn"
        assert r.new_value == "btn active"

    def test_child_list_with_nodes(self):
        r = MutationRecord(
            mutation_type=MutationType.CHILD_LIST,
            target_id="parent",
            added_nodes=["child-1", "child-2"],
            removed_nodes=["old-child"],
        )
        assert r.added_nodes == ["child-1", "child-2"]
        assert r.removed_nodes == ["old-child"]

    def test_to_dict_child_list(self):
        r = MutationRecord(
            mutation_type=MutationType.CHILD_LIST,
            target_id="el-1",
            added_nodes=["el-2"],
            removed_nodes=["el-3"],
        )
        d = r.to_dict()
        assert d["type"] == "CHILD_LIST"
        assert d["targetId"] == "el-1"
        assert d["addedNodes"] == ["el-2"]
        assert d["removedNodes"] == ["el-3"]
        assert d["attributeName"] is None
        assert d["oldValue"] is None
        assert d["newValue"] is None

    def test_to_dict_attributes(self):
        r = MutationRecord(
            mutation_type=MutationType.ATTRIBUTES,
            target_id="el-5",
            attribute_name="href",
            old_value="/old",
            new_value="/new",
        )
        d = r.to_dict()
        assert d["type"] == "ATTRIBUTES"
        assert d["attributeName"] == "href"
        assert d["oldValue"] == "/old"
        assert d["newValue"] == "/new"

    def test_to_dict_character_data(self):
        r = MutationRecord(
            mutation_type=MutationType.CHARACTER_DATA,
            target_id="text-1",
            old_value="hello",
            new_value="world",
        )
        d = r.to_dict()
        assert d["type"] == "CHARACTER_DATA"
        assert d["oldValue"] == "hello"
        assert d["newValue"] == "world"

    def test_to_dict_is_serialisable(self):
        import json
        r = MutationRecord(MutationType.CHILD_LIST, "el-1", added_nodes=["el-2"])
        json.dumps(r.to_dict())  # must not raise


class TestMutationObserver:
    def test_empty_on_init(self):
        obs = MutationObserver()
        assert obs.count() == 0

    def test_record_increments_count(self):
        obs = MutationObserver()
        obs.record(MutationRecord(MutationType.CHILD_LIST, "el-1"))
        assert obs.count() == 1
        obs.record(MutationRecord(MutationType.ATTRIBUTES, "el-2"))
        assert obs.count() == 2

    def test_collect_returns_records(self):
        obs = MutationObserver()
        r1 = MutationRecord(MutationType.CHILD_LIST, "el-1", added_nodes=["el-2"])
        r2 = MutationRecord(MutationType.ATTRIBUTES, "el-1", attribute_name="class")
        obs.record(r1)
        obs.record(r2)
        collected = obs.collect()
        assert len(collected) == 2
        assert collected[0] is r1
        assert collected[1] is r2

    def test_collect_clears_records(self):
        obs = MutationObserver()
        obs.record(MutationRecord(MutationType.CHILD_LIST, "el-1"))
        obs.collect()
        assert obs.count() == 0
        assert obs.collect() == []

    def test_collect_returns_copy(self):
        obs = MutationObserver()
        obs.record(MutationRecord(MutationType.CHILD_LIST, "el-1"))
        first = obs.collect()
        second = obs.collect()
        assert first != second  # second should be empty
        assert len(first) == 1
        assert len(second) == 0

    def test_multiple_collect_cycles(self):
        obs = MutationObserver()
        obs.record(MutationRecord(MutationType.CHILD_LIST, "el-1"))
        obs.collect()
        obs.record(MutationRecord(MutationType.ATTRIBUTES, "el-2"))
        obs.record(MutationRecord(MutationType.CHARACTER_DATA, "el-3"))
        second = obs.collect()
        assert len(second) == 2
        assert second[0].mutation_type == MutationType.ATTRIBUTES

    def test_record_order_preserved(self):
        obs = MutationObserver()
        for i in range(5):
            obs.record(MutationRecord(MutationType.CHILD_LIST, f"el-{i}"))
        collected = obs.collect()
        ids = [r.target_id for r in collected]
        assert ids == ["el-0", "el-1", "el-2", "el-3", "el-4"]
