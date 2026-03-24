"""Unit tests for affordance scoring and action ranking."""
from __future__ import annotations

import pytest
from an_web.semantic.affordances import (
    score_action_node,
    rank_primary_actions,
    _HIGH_VALUE_TEXTS,
    _LOW_VALUE_TEXTS,
    _HIGH_VALUE_CLASSES,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _node(
    role="button",
    name="",
    tag="button",
    affordances=None,
    attrs=None,
    interaction_rank=None,
):
    d = {
        "role": role,
        "name": name,
        "tag": tag,
        "affordances": affordances or [],
        "attributes": attrs or {},
    }
    if interaction_rank is not None:
        d["interaction_rank"] = interaction_rank
    return d


# ── score_action_node ──────────────────────────────────────────────────────────

class TestScoreActionNode:
    def test_score_in_range(self):
        n = _node(role="button", name="Submit")
        s = score_action_node(n)
        assert 0.0 <= s <= 1.0

    def test_button_higher_than_link(self):
        btn = _node(role="button", name="OK")
        lnk = _node(role="link", name="OK")
        assert score_action_node(btn) > score_action_node(lnk)

    def test_link_higher_than_textbox(self):
        lnk = _node(role="link", name="Go")
        txt = _node(role="textbox", name="Enter text")
        assert score_action_node(lnk) > score_action_node(txt)

    def test_high_value_name_boosts_score(self):
        with_kw = _node(role="button", name="Login")
        without_kw = _node(role="button", name="Button")
        assert score_action_node(with_kw) > score_action_node(without_kw)

    def test_submit_keyword_is_high_value(self):
        n = _node(role="button", name="submit")
        assert score_action_node(n) > score_action_node(_node(role="button", name=""))

    def test_checkout_keyword_is_high_value(self):
        n = _node(role="button", name="proceed to checkout")
        assert score_action_node(n) > score_action_node(_node(role="button", name=""))

    def test_cancel_penalty(self):
        cancel = _node(role="button", name="Cancel")
        ok = _node(role="button", name="OK")
        assert score_action_node(cancel) < score_action_node(ok)

    def test_close_penalty(self):
        close = _node(role="button", name="Close")
        submit = _node(role="button", name="Submit")
        assert score_action_node(close) < score_action_node(submit)

    def test_submit_input_type_bonus(self):
        submit_input = _node(role="button", tag="input", attrs={"type": "submit"})
        normal_button = _node(role="button", tag="button")
        assert score_action_node(submit_input) > score_action_node(normal_button)

    def test_submit_affordance_bonus(self):
        with_submit = _node(role="button", affordances=["click", "submit"])
        without_submit = _node(role="button", affordances=["click"])
        assert score_action_node(with_submit) > score_action_node(without_submit)

    def test_primary_css_class_bonus(self):
        primary = _node(role="button", attrs={"class": "btn-primary"})
        secondary = _node(role="button", attrs={"class": "btn-secondary"})
        assert score_action_node(primary) > score_action_node(secondary)

    def test_cta_css_class_bonus(self):
        cta = _node(role="button", attrs={"class": "cta-button"})
        plain = _node(role="button", attrs={})
        assert score_action_node(cta) > score_action_node(plain)

    def test_interaction_rank_bonus(self):
        high_rank = _node(role="button", interaction_rank=1.0)
        low_rank = _node(role="button", interaction_rank=0.0)
        assert score_action_node(high_rank) > score_action_node(low_rank)

    def test_accessible_name_presence_bonus(self):
        named = _node(role="button", name="Submit form")
        unnamed = _node(role="button", name="")
        assert score_action_node(named) > score_action_node(unnamed)

    def test_empty_node_low_score(self):
        empty = _node(role="generic", name="", tag="div")
        s = score_action_node(empty)
        assert s < 0.20  # generic with no name or submit affordance

    def test_score_never_below_zero(self):
        """Low-value penalties should not produce negative scores."""
        worst = _node(role="generic", name="cancel close dismiss", tag="span")
        assert score_action_node(worst) >= 0.0

    def test_score_never_above_one(self):
        """All bonuses stacked should not exceed 1.0."""
        best = _node(
            role="button", name="login",
            tag="input", attrs={"type": "submit", "class": "btn-primary"},
            affordances=["click", "submit"],
            interaction_rank=1.0,
        )
        assert score_action_node(best) <= 1.0


# ── rank_primary_actions ───────────────────────────────────────────────────────

class TestRankPrimaryActions:
    def test_empty_input_returns_empty(self):
        assert rank_primary_actions([]) == []

    def test_returns_top_k(self):
        nodes = [_node(role="button", name=f"btn{i}") for i in range(10)]
        result = rank_primary_actions(nodes, top_k=3)
        assert len(result) <= 3

    def test_sorted_by_score_desc(self):
        nodes = [
            _node(role="button", name="Login"),       # high value
            _node(role="button", name="Cancel"),      # penalty
            _node(role="button", name="Submit form"), # high value
            _node(role="link", name="Read more"),     # medium
        ]
        ranked = rank_primary_actions(nodes, top_k=4)
        scores = [score_action_node(n) for n in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_default_top_k_is_5(self):
        nodes = [_node(role="button", name=f"btn{i}") for i in range(10)]
        result = rank_primary_actions(nodes)
        assert len(result) <= 5

    def test_nodes_not_mutated(self):
        original = _node(role="button", name="Submit")
        original_copy = dict(original)
        rank_primary_actions([original])
        assert original == original_copy

    def test_high_value_button_ranked_first(self):
        nodes = [
            _node(role="link", name="click here"),
            _node(role="button", name="Submit", affordances=["click", "submit"]),
            _node(role="textbox", name="Enter value"),
        ]
        ranked = rank_primary_actions(nodes, top_k=3)
        # Submit button should come first
        assert ranked[0]["name"] == "Submit"


# ── Vocabulary sets ────────────────────────────────────────────────────────────

class TestVocabularySets:
    def test_high_value_texts_nonempty(self):
        assert len(_HIGH_VALUE_TEXTS) > 0

    def test_low_value_texts_nonempty(self):
        assert len(_LOW_VALUE_TEXTS) > 0

    def test_high_value_classes_nonempty(self):
        assert len(_HIGH_VALUE_CLASSES) > 0

    def test_login_in_high_value(self):
        assert "login" in _HIGH_VALUE_TEXTS

    def test_submit_in_high_value(self):
        assert "submit" in _HIGH_VALUE_TEXTS

    def test_cancel_in_low_value(self):
        assert "cancel" in _LOW_VALUE_TEXTS

    def test_primary_in_high_value_classes(self):
        assert "primary" in _HIGH_VALUE_CLASSES
