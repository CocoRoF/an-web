"""Unit tests for page type classifier."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Document
from an_web.dom.semantics import SemanticNode
from an_web.semantic.page_type import (
    PageTypeResult,
    classify_page_type,
    classify_page_type_full,
)


# ── Minimal SemanticNode tree helper ──────────────────────────────────────────

def _empty_tree() -> SemanticNode:
    return SemanticNode(
        node_id="root", tag="document", role="RootWebArea",
        name=None, value=None, xpath="/",
        is_interactive=False, visible=True,
    )


# ── PageTypeResult dataclass ───────────────────────────────────────────────────

class TestPageTypeResult:
    def test_str_representation(self):
        r = PageTypeResult(page_type="login_form", confidence=0.85)
        s = str(r)
        assert "login_form" in s
        assert "0.85" in s

    def test_signals_default_empty(self):
        r = PageTypeResult(page_type="search", confidence=0.70)
        assert r.signals == []

    def test_signals_stored(self):
        r = PageTypeResult(
            page_type="checkout", confidence=0.90,
            signals=["url_pattern:checkout→checkout"]
        )
        assert len(r.signals) == 1


# ── URL-based classification ───────────────────────────────────────────────────

class TestURLPatterns:
    def test_login_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://example.com/login")
        assert result.page_type == "login_form"
        assert result.confidence > 0.5
        assert any("url_pattern" in s for s in result.signals)

    def test_signin_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/signin")
        assert result.page_type == "login_form"

    def test_register_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/register")
        assert result.page_type == "registration_form"

    def test_signup_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/signup")
        assert result.page_type == "registration_form"

    def test_checkout_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://shop.com/checkout")
        assert result.page_type == "checkout"

    def test_cart_url(self):
        tree = _empty_tree()
        # Use a neutral domain to avoid listing keywords in hostname
        result = classify_page_type_full(tree, url="https://myapp.com/cart")
        assert result.page_type == "checkout"

    def test_search_results_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://site.com/search?q=foo")
        assert result.page_type == "search_results"

    def test_search_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://site.com/search")
        assert result.page_type in ("search", "search_results")

    def test_dashboard_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/dashboard")
        assert result.page_type == "dashboard"

    def test_settings_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/settings")
        assert result.page_type == "settings"

    def test_profile_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/profile")
        assert result.page_type == "profile"

    def test_error_url_404(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/404")
        assert result.page_type == "error"

    def test_article_url(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://blog.com/post/my-article")
        assert result.page_type == "article"


# ── Title-based classification ─────────────────────────────────────────────────

class TestTitlePatterns:
    def test_login_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Login - MyApp")
        assert result.page_type == "login_form"

    def test_sign_in_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Sign in to continue")
        assert result.page_type == "login_form"

    def test_checkout_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Checkout - Your Cart")
        assert result.page_type == "checkout"

    def test_dashboard_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Dashboard")
        assert result.page_type == "dashboard"

    def test_settings_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Settings | Account")
        assert result.page_type == "settings"

    def test_404_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Page Not Found")
        assert result.page_type == "error"

    def test_search_results_title(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="Search results for Python")
        assert result.page_type == "search_results"


# ── Fallback / edge cases ─────────────────────────────────────────────────────

class TestFallback:
    def test_empty_url_and_title_returns_empty(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, title="", url="")
        assert result.page_type == "empty"
        assert result.confidence == 1.0

    def test_unknown_url_returns_generic(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://example.com/about-us")
        assert result.page_type == "generic"

    def test_confidence_is_float_in_range(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/login")
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_rounded_to_3_places(self):
        tree = _empty_tree()
        result = classify_page_type_full(tree, url="https://app.com/login")
        # Should be rounded to 3 decimal places
        assert result.confidence == round(result.confidence, 3)

    def test_classify_page_type_returns_str(self):
        tree = _empty_tree()
        result = classify_page_type(tree, url="https://app.com/login")
        assert isinstance(result, str)
        assert result == "login_form"


# ── URL + Title combined scoring ──────────────────────────────────────────────

class TestCombinedScoring:
    def test_url_and_title_agreement_boosts_confidence(self):
        tree = _empty_tree()
        # Both URL and title agree on login_form
        result_combined = classify_page_type_full(
            tree,
            url="https://app.com/login",
            title="Login to MyApp",
        )
        # URL only
        result_url_only = classify_page_type_full(
            tree, url="https://app.com/login"
        )
        assert result_combined.confidence >= result_url_only.confidence

    def test_url_takes_priority_over_title(self):
        tree = _empty_tree()
        # URL says settings, title says login
        result = classify_page_type_full(
            tree,
            url="https://app.com/settings",
            title="Login",
        )
        # URL has higher weight (1.0 vs 0.7) so settings should win
        # Settings URL conf=0.85 → score=0.85, Login title conf=0.80 → score=0.56
        assert result.page_type == "settings"

    def test_signals_list_populated(self):
        tree = _empty_tree()
        result = classify_page_type_full(
            tree, url="https://app.com/login", title="Login page"
        )
        assert len(result.signals) >= 1
        assert any("url_pattern" in s for s in result.signals)


# ── Semantic tree signals ──────────────────────────────────────────────────────

class TestSemanticTreeSignals:
    def _make_node(self, role, name=None, attrs=None, is_interactive=False):
        return SemanticNode(
            node_id="n1", tag="div", role=role, name=name, value=None,
            xpath="/", is_interactive=is_interactive, visible=True,
            attributes=attrs or {},
        )

    def test_password_input_signals_login(self):
        """A tree with a password input should lean toward login_form."""
        tree = _empty_tree()
        pw_node = SemanticNode(
            node_id="pw", tag="input", role="textbox", name="Password",
            value=None, xpath="/input[1]", is_interactive=True, visible=True,
            attributes={"type": "password"},
        )
        tree.children.append(pw_node)

        result = classify_page_type_full(tree, url="", title="")
        # Semantic signals should push toward login_form
        assert any("password" in s for s in result.signals)

    def test_search_input_signals_search(self):
        """A tree with a searchbox input should get a search signal."""
        tree = _empty_tree()
        search_node = SemanticNode(
            node_id="s1", tag="input", role="searchbox", name="Search",
            value=None, xpath="/input[1]", is_interactive=True, visible=True,
            attributes={"type": "search"},
        )
        tree.children.append(search_node)

        result = classify_page_type_full(tree, url="", title="")
        assert any("search" in s for s in result.signals)

    def test_single_article_signals_article(self):
        """A tree with one <article> node should get article signal."""
        tree = _empty_tree()
        article_node = SemanticNode(
            node_id="a1", tag="article", role="article", name=None,
            value=None, xpath="/article[1]", is_interactive=False, visible=True,
        )
        tree.children.append(article_node)

        result = classify_page_type_full(tree, url="", title="")
        assert any("article" in s for s in result.signals)

    def test_many_articles_signals_listing(self):
        """Multiple <article> nodes → listing signal."""
        tree = _empty_tree()
        for i in range(3):
            art = SemanticNode(
                node_id=f"a{i}", tag="article", role="article", name=None,
                value=None, xpath=f"/article[{i+1}]", is_interactive=False, visible=True,
            )
            tree.children.append(art)

        result = classify_page_type_full(tree, url="", title="")
        assert any("multi_article" in s for s in result.signals)
