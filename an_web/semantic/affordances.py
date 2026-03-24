"""Action affordance inference — what can AI do with each element?"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.nodes import Element


def infer_affordances(element: Element, role: str) -> list[str]:
    """Return list of AI-executable actions for an element."""
    from an_web.semantic.roles import get_affordances
    return get_affordances(role, element)


def rank_primary_actions(
    interactive_nodes: list[dict],
) -> list[dict]:
    """
    Rank interactive nodes by likely AI relevance.

    Primary CTA heuristics:
    - Submit buttons > regular buttons > links
    - Buttons with "submit", "login", "continue", "next" text score higher
    - Buttons with class containing "primary", "main", "cta" score higher
    """
    HIGH_VALUE_TEXTS = {
        "login", "log in", "sign in", "signin",
        "submit", "continue", "next", "proceed",
        "search", "find", "confirm", "ok", "done",
        "register", "sign up", "create account",
        "buy", "purchase", "checkout", "add to cart",
    }

    def score(node: dict) -> float:
        s = 0.0
        name = (node.get("name") or "").lower()
        role = node.get("role", "")
        tag = node.get("tag", "")

        if role == "button":
            s += 10
        if tag == "input" and node.get("attributes", {}).get("type") == "submit":
            s += 15
        if any(kw in name for kw in HIGH_VALUE_TEXTS):
            s += 20

        classes = node.get("attributes", {}).get("class", "")
        if any(k in classes for k in ("primary", "main", "cta", "submit")):
            s += 10

        return s

    return sorted(interactive_nodes, key=score, reverse=True)
