"""
Page type classifier for AN-Web semantic layer.

Classifies the current page into a type category that helps AI agents
plan their next actions. Classification uses a multi-signal approach:

  1. URL path patterns (highest priority — most reliable)
  2. Page title keywords
  3. Semantic tree signals (form structure, content patterns)
  4. Landmark structure (how many <nav>, <main>, <article> elements)
  5. Content density signals (heading count, list depth, link count)

Output types:
  login_form       — username/password form
  registration_form — signup/join form
  search           — standalone search bar page
  search_results   — search results listing
  listing          — product/content listing (shop, catalog)
  product_detail   — single product/item detail view
  article          — blog post, news article, documentation
  checkout         — cart, payment, order confirmation flow
  form             — generic multi-field form (contact, settings, survey)
  dashboard        — overview/analytics page with stats
  profile          — user profile page
  settings         — app settings/preferences page
  error            — 4xx/5xx error pages
  empty            — blank page / no content
  generic          — fallback when no pattern matches

Each result includes a confidence score (0.0–1.0) and a list of
matched signals for traceability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from an_web.dom.semantics import SemanticNode


# ── URL keyword patterns ───────────────────────────────────────────────────────

_URL_PATTERNS: list[tuple[frozenset[str], str, float]] = [
    # (keywords_in_url, page_type, base_confidence)
    (frozenset({"login", "signin", "sign-in", "auth/login", "session"}),  "login_form",       0.90),
    (frozenset({"signup", "register", "join", "create-account", "enroll"}), "registration_form", 0.90),
    (frozenset({"checkout", "payment", "order/confirm", "cart/checkout"}), "checkout",         0.90),
    (frozenset({"cart", "basket", "bag"}),                                 "checkout",         0.75),
    (frozenset({"search/results", "search?q=", "results?q="}),            "search_results",   0.90),
    (frozenset({"search", "find", "browse?q"}),                            "search",           0.75),
    (frozenset({"product/", "/products/", "/item/", "/listing/"}),        "product_detail",   0.80),
    (frozenset({"shop", "catalog", "store", "/products", "/items"}),      "listing",          0.75),
    (frozenset({"blog/", "article/", "/news/", "/post/", "/posts/"}),     "article",          0.80),
    (frozenset({"dashboard", "/dash/", "/overview"}),                     "dashboard",        0.85),
    (frozenset({"/profile", "/user/", "/account/", "/me/"}),              "profile",          0.80),
    (frozenset({"/settings", "/preferences", "/config"}),                 "settings",         0.85),
    (frozenset({"404", "not-found", "error", "/error/", "500", "403"}),   "error",            0.90),
]

# ── Title keyword patterns ────────────────────────────────────────────────────

_TITLE_PATTERNS: list[tuple[frozenset[str], str, float]] = [
    (frozenset({"login", "sign in", "log in", "signin", "welcome back"}), "login_form",       0.80),
    (frozenset({"register", "sign up", "create account", "join us"}),     "registration_form", 0.80),
    (frozenset({"checkout", "payment", "your cart", "order summary"}),    "checkout",         0.80),
    (frozenset({"search results", "results for"}),                        "search_results",   0.80),
    (frozenset({"search", "find"}),                                       "search",           0.60),
    (frozenset({"dashboard", "overview", "stats", "analytics"}),          "dashboard",        0.75),
    (frozenset({"profile", "my account", "account settings"}),            "profile",          0.70),
    (frozenset({"settings", "preferences"}),                              "settings",         0.75),
    (frozenset({"page not found", "404", "error", "something went wrong",
                "403 forbidden", "500 internal"}),                        "error",            0.85),
    (frozenset({"home", "welcome to", "get started"}),                    "generic",          0.50),
]


# ── PageTypeResult ─────────────────────────────────────────────────────────────

@dataclass
class PageTypeResult:
    """
    Full result of page type classification.

    Attributes:
        page_type:  String identifier (e.g. 'login_form', 'listing').
        confidence: 0.0–1.0. Higher = more reliable classification.
        signals:    List of human-readable signals that fired.
    """
    page_type: str
    confidence: float
    signals: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"PageTypeResult(type={self.page_type!r}, confidence={self.confidence:.2f})"


# ── Main classifier ────────────────────────────────────────────────────────────

def classify_page_type(
    tree: "SemanticNode",
    title: str = "",
    url: str = "",
) -> str:
    """
    Classify page type — returns the page_type string directly.

    For the full result including confidence and signals, use classify_page_type_full().
    """
    return classify_page_type_full(tree, title=title, url=url).page_type


def classify_page_type_full(
    tree: "SemanticNode",
    title: str = "",
    url: str = "",
) -> PageTypeResult:
    """
    Full page type classification with confidence score and signals.

    Uses a scoring approach: collect evidence from multiple signals,
    find the type with the highest combined score.
    """
    url_lower = url.lower()
    title_lower = title.lower()
    signals: list[str] = []

    # ── 1. URL pattern matching ────────────────────────────────────────────
    url_votes: dict[str, float] = {}
    for keywords, ptype, conf in _URL_PATTERNS:
        for kw in keywords:
            if kw in url_lower:
                url_votes[ptype] = max(url_votes.get(ptype, 0.0), conf)
                signals.append(f"url_pattern:{kw}→{ptype}")
                break

    # ── 2. Title pattern matching ─────────────────────────────────────────
    title_votes: dict[str, float] = {}
    for keywords, ptype, conf in _TITLE_PATTERNS:
        for kw in keywords:
            if kw in title_lower:
                title_votes[ptype] = max(title_votes.get(ptype, 0.0), conf)
                signals.append(f"title_keyword:{kw!r}→{ptype}")
                break

    # ── 3. Semantic tree signals ───────────────────────────────────────────
    sem_votes: dict[str, float] = {}
    _classify_from_tree(tree, sem_votes, signals)

    # ── 4. Combine votes (URL > title > semantic) ─────────────────────────
    all_types: set[str] = set(url_votes) | set(title_votes) | set(sem_votes)

    best_type = "generic"
    best_score = 0.0

    for ptype in all_types:
        # Weighted combination: URL=1.0, title=0.7, semantic=0.5
        score = (
            url_votes.get(ptype, 0.0) * 1.0 +
            title_votes.get(ptype, 0.0) * 0.7 +
            sem_votes.get(ptype, 0.0) * 0.5
        )
        if score > best_score:
            best_score = score
            best_type = ptype

    # Normalise confidence to [0, 1]
    confidence = min(1.0, best_score / 1.5)

    if best_type == "generic" and not title and not url:
        best_type = "empty"
        confidence = 1.0

    return PageTypeResult(
        page_type=best_type,
        confidence=round(confidence, 3),
        signals=signals,
    )


def _classify_from_tree(
    tree: "SemanticNode",
    votes: dict[str, float],
    signals: list[str],
) -> None:
    """
    Analyse the semantic tree for structural page type signals.

    Modifies ``votes`` in-place.
    """
    interactive = tree.find_interactive()
    inputs = [n for n in interactive if n.role in (
        "textbox", "combobox", "searchbox", "spinbutton", "checkbox", "radio"
    )]
    buttons = [n for n in interactive if n.role == "button"]
    links = [n for n in interactive if n.role == "link"]

    # Password input → strong login signal
    password_inputs = [
        n for n in inputs
        if n.attributes.get("type") == "password"
        or (n.name and any(k in n.name.lower() for k in ("password", "pwd", "pass")))
    ]
    if password_inputs:
        votes["login_form"] = max(votes.get("login_form", 0.0), 0.70)
        signals.append(f"semantic:password_input({len(password_inputs)})")

    # Search input
    search_inputs = [
        n for n in inputs
        if n.role == "searchbox"
        or (n.name and any(k in n.name.lower() for k in ("search", "query", "find")))
        or n.attributes.get("type") == "search"
    ]
    if search_inputs:
        votes["search"] = max(votes.get("search", 0.0), 0.65)
        signals.append(f"semantic:search_input({len(search_inputs)})")

    # Multiple inputs + button → form-like
    if len(inputs) >= 3 and buttons:
        # Distinguish registration from generic form
        email_inputs = [n for n in inputs if n.attributes.get("type") in ("email",)]
        if email_inputs and password_inputs:
            votes["registration_form"] = max(votes.get("registration_form", 0.0), 0.60)
            signals.append("semantic:email+password→registration")
        else:
            votes["form"] = max(votes.get("form", 0.0), 0.55)
            signals.append(f"semantic:multi_input_form({len(inputs)})")
    elif len(inputs) >= 1 and buttons:
        if not password_inputs and not search_inputs:
            votes["form"] = max(votes.get("form", 0.0), 0.40)

    # Many listitem → listing or article
    list_items = tree.find_by_role("listitem")
    if len(list_items) >= 8:
        votes["listing"] = max(votes.get("listing", 0.0), 0.50)
        signals.append(f"semantic:many_listitem({len(list_items)})")

    # Articles / headings → article or listing
    articles = tree.find_by_role("article")
    if articles:
        if len(articles) == 1:
            votes["article"] = max(votes.get("article", 0.0), 0.60)
            signals.append("semantic:single_article")
        else:
            votes["listing"] = max(votes.get("listing", 0.0), 0.55)
            signals.append(f"semantic:multi_article({len(articles)})")

    # Heading count and structure
    headings = tree.find_by_role("heading")
    if len(headings) == 1:
        # Single heading — could be article or landing
        pass
    elif len(headings) >= 5:
        # Many headings → article-like content
        votes["article"] = max(votes.get("article", 0.0), 0.45)
        signals.append(f"semantic:heading_rich({len(headings)})")

    # High link count → navigation-heavy page (listing or article)
    if len(links) >= 10:
        votes["listing"] = max(votes.get("listing", 0.0), 0.35)
        signals.append(f"semantic:many_links({len(links)})")

    # Dialog / modal blocker → overlay page
    dialogs = tree.find_by_role("dialog")
    if dialogs:
        signals.append(f"semantic:dialog({len(dialogs)})")

    # Price-like content in names → product/checkout
    for node in tree.find_interactive():
        name = (node.name or "").lower()
        if "price" in name or "total" in name or "$" in name or "€" in name:
            votes["checkout"] = max(votes.get("checkout", 0.0), 0.45)
            signals.append("semantic:price_signal")
            break
