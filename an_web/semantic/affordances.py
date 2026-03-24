"""
Action affordance inference — what can AI do with each element?

Affordances are the bridge between the semantic layer and the actions layer.
The goal is not exhaustive completeness but AI-actionability: what operations
make sense for an AI agent to attempt on this element?

Primary exports:
    infer_affordances(element, role) → list[str]
    rank_primary_actions(nodes)       → list[dict]   (sorted by AI relevance)
    score_action_node(node)           → float         (0.0–1.0 relevance score)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from an_web.dom.nodes import Element


def infer_affordances(element: Element, role: str) -> list[str]:
    """
    Return list of AI-executable actions for an element.

    Delegates to the comprehensive get_affordances() in roles.py.
    This is the public entry point from extractor.py.
    """
    from an_web.semantic.roles import get_affordances
    return get_affordances(role, element)


# ── Vocabulary of high-value action keywords ───────────────────────────────────

_HIGH_VALUE_TEXTS: frozenset[str] = frozenset({
    # Auth
    "login", "log in", "sign in", "signin", "log into",
    "register", "sign up", "signup", "create account", "join",
    # Navigation / progress
    "submit", "continue", "next", "proceed", "finish", "done",
    "confirm", "ok", "yes", "accept", "agree",
    # Search
    "search", "find", "filter", "go", "explore", "browse",
    # Commerce
    "buy", "purchase", "checkout", "add to cart", "order", "pay",
    "add to bag", "shop now", "get started",
    # Account
    "update", "save", "apply", "send", "download", "subscribe",
})

_HIGH_VALUE_CLASSES: frozenset[str] = frozenset({
    "primary", "main", "cta", "submit", "btn-primary", "button-primary",
    "action", "btn-cta", "call-to-action",
})

_LOW_VALUE_TEXTS: frozenset[str] = frozenset({
    "cancel", "close", "dismiss", "back", "skip", "no", "not now",
    "maybe later", "decline",
})


def score_action_node(node: dict[str, Any]) -> float:
    """
    Compute AI relevance score for an interactive node (0.0–1.0).

    Used by rank_primary_actions() to sort primary CTA candidates.

    Scoring components:
    - Role: button > link > textbox > other
    - Name keywords: login/submit/checkout/search > generic
    - CSS classes: primary/cta bonus
    - Input type: submit bonus
    - Low-value keywords: cancel/close penalty
    - Interaction rank (if available from LayoutEngine assessment)
    """
    score = 0.0
    name = (node.get("name") or "").lower()
    role = node.get("role", "")
    tag = node.get("tag", "")
    attrs = node.get("attributes", {})
    affordances = node.get("affordances", [])

    # ── Role bonus ─────────────────────────────────────────────────────────
    if role == "button":
        score += 0.30
    elif role == "link":
        score += 0.20
    elif role in ("textbox", "searchbox"):
        score += 0.15
    elif role == "combobox":
        score += 0.10
    elif role in ("checkbox", "radio"):
        score += 0.05

    # ── Submit input bonus ─────────────────────────────────────────────────
    if tag == "input" and attrs.get("type") == "submit":
        score += 0.20

    # ── Explicit submit button bonus ───────────────────────────────────────
    if "submit" in affordances:
        score += 0.15

    # ── Name keyword bonus ─────────────────────────────────────────────────
    if name:
        for kw in _HIGH_VALUE_TEXTS:
            if kw in name:
                score += 0.25
                break  # only one keyword bonus

        # Low-value penalty
        for kw in _LOW_VALUE_TEXTS:
            if kw in name:
                score -= 0.20
                break

    # ── CSS class bonus ───────────────────────────────────────────────────
    classes = (attrs.get("class") or "").lower()
    for cls in _HIGH_VALUE_CLASSES:
        if cls in classes:
            score += 0.10
            break

    # ── Layout interaction_rank bonus (from LayoutEngine) ─────────────────
    rank = node.get("interaction_rank", None)
    if rank is not None:
        score += float(rank) * 0.10

    # ── Accessible name presence bonus ────────────────────────────────────
    if name:
        score += 0.05

    return max(0.0, min(1.0, score))


def rank_primary_actions(
    interactive_nodes: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Rank interactive nodes by AI action relevance.

    Returns top_k nodes sorted by score descending.
    Each node dict is passed through unchanged (no mutation).

    Args:
        interactive_nodes: List of node dicts (to_dict() output from SemanticNode).
        top_k:             Max number of results to return.

    Returns:
        Sorted list of the most relevant action targets.
    """
    if not interactive_nodes:
        return []

    scored = sorted(interactive_nodes, key=score_action_node, reverse=True)
    return scored[:top_k]
