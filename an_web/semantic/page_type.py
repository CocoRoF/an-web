"""Page type classifier — login/search/listing/detail/checkout/error."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.semantics import SemanticNode


def classify_page_type(tree: SemanticNode, title: str = "", url: str = "") -> str:
    """
    Classify the current page type for AI planning assistance.

    Heuristics based on:
    - URL patterns
    - Page title keywords
    - Form structure
    - Interactive element patterns
    """
    url_lower = url.lower()
    title_lower = title.lower()

    # URL-based hints
    for keyword in ("login", "signin", "sign-in", "auth"):
        if keyword in url_lower:
            return "login_form"
    for keyword in ("signup", "register", "join"):
        if keyword in url_lower:
            return "registration_form"
    for keyword in ("search", "query", "find", "results"):
        if keyword in url_lower:
            return "search_results"
    for keyword in ("checkout", "payment", "cart", "order"):
        if keyword in url_lower:
            return "checkout"

    # Content-based classification
    interactive = tree.find_interactive()
    inputs = [n for n in interactive if n.role in ("textbox", "combobox", "searchbox")]
    buttons = [n for n in interactive if n.role == "button"]
    password_inputs = [
        n for n in inputs
        if n.name and any(k in n.name.lower() for k in ("password", "pwd", "pass"))
    ]

    # Login form: password field + submit button
    if password_inputs and buttons:
        return "login_form"

    # Search: search input prominent
    search_inputs = [
        n for n in inputs
        if n.role == "searchbox" or (
            n.name and any(k in n.name.lower() for k in ("search", "query", "find"))
        )
    ]
    if search_inputs:
        return "search"

    # Form page: inputs + button but no password
    if len(inputs) >= 2 and buttons:
        return "form"

    # Listing: many similar items
    list_nodes = tree.find_by_role("listitem")
    if len(list_nodes) > 5:
        return "listing"

    # Error page
    for keyword in ("error", "not found", "404", "forbidden", "403"):
        if keyword in title_lower:
            return "error"

    return "generic"
