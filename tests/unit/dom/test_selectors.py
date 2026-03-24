"""Unit tests for SelectorEngine."""
from __future__ import annotations

import pytest
from an_web.browser.parser import parse_html
from an_web.dom.selectors import SelectorEngine


def qs(html: str, selector: str):
    doc = parse_html(html)
    return SelectorEngine().query_selector(doc, selector)


def qsa(html: str, selector: str):
    doc = parse_html(html)
    return SelectorEngine().query_selector_all(doc, selector)


# ─── Type selector ────────────────────────────────────────────────────────────

class TestTypeSelector:
    def test_tag_match(self):
        els = qsa("<p>a</p><p>b</p>", "p")
        assert len(els) == 2

    def test_tag_no_match(self):
        assert qs("<div>x</div>", "span") is None

    def test_tag_case_insensitive(self):
        assert qs("<BUTTON>x</BUTTON>", "button") is not None

    def test_first_match(self):
        el = qs("<p id='a'>first</p><p id='b'>second</p>", "p")
        assert el.get_id() == "a"


# ─── ID selector ──────────────────────────────────────────────────────────────

class TestIdSelector:
    def test_id_match(self):
        el = qs("<input id='email' type='email'>", "#email")
        assert el is not None
        assert el.get_id() == "email"

    def test_id_no_match(self):
        assert qs("<input id='email'>", "#phone") is None

    def test_id_unique(self):
        els = qsa("<div id='a'></div><div id='a'></div>", "#a")
        assert len(els) >= 1  # returns first match at minimum


# ─── Class selector ───────────────────────────────────────────────────────────

class TestClassSelector:
    def test_class_match(self):
        el = qs("<button class='btn-primary'>Go</button>", ".btn-primary")
        assert el is not None

    def test_class_no_match(self):
        assert qs("<button class='btn-secondary'>Go</button>", ".btn-primary") is None

    def test_multiple_classes(self):
        el = qs("<div class='foo bar baz'>x</div>", ".bar")
        assert el is not None

    def test_two_class_selectors(self):
        html = "<div class='foo bar'>match</div><div class='foo'>no-match</div>"
        els = qsa(html, ".foo.bar")
        assert len(els) == 1


# ─── Attribute selector ───────────────────────────────────────────────────────

class TestAttributeSelector:
    def test_attr_exists(self):
        el = qs("<input disabled>", "[disabled]")
        assert el is not None

    def test_attr_equals(self):
        el = qs("<input type='email'>", "[type=email]")
        assert el is not None

    def test_attr_equals_quoted(self):
        el = qs("<input type='email'>", '[type="email"]')
        assert el is not None

    def test_attr_not_match(self):
        assert qs("<input type='text'>", "[type=email]") is None

    def test_attr_starts_with(self):
        el = qs("<a href='https://example.com'>link</a>", "[href^=https]")
        assert el is not None

    def test_attr_ends_with(self):
        el = qs("<img src='logo.png'>", "[src$=.png]")
        assert el is not None

    def test_attr_contains(self):
        el = qs("<input name='user_email'>", "[name*=email]")
        assert el is not None

    def test_attr_space_separated(self):
        el = qs("<div class='foo bar'>x</div>", "[class~=foo]")
        assert el is not None


# ─── Compound selector ────────────────────────────────────────────────────────

class TestCompoundSelector:
    def test_tag_and_id(self):
        el = qs("<input id='q' type='text'>", "input#q")
        assert el is not None

    def test_tag_and_class(self):
        el = qs("<button class='btn'>Go</button>", "button.btn")
        assert el is not None

    def test_tag_and_attr(self):
        el = qs("<input type='submit' value='Go'>", "input[type=submit]")
        assert el is not None

    def test_tag_class_attr(self):
        el = qs("<input class='fancy' type='text'>", "input.fancy[type=text]")
        assert el is not None

    def test_compound_no_match(self):
        assert qs("<input type='text'>", "button[type=text]") is None


# ─── Descendant combinator ────────────────────────────────────────────────────

class TestDescendantCombinator:
    def test_form_input(self):
        html = "<form><input type='email' name='e'></form>"
        el = qs(html, "form input")
        assert el is not None
        assert el.tag == "input"

    def test_nested_descendant(self):
        html = "<div><section><p id='deep'>x</p></section></div>"
        el = qs(html, "div p")
        assert el is not None
        assert el.get_id() == "deep"

    def test_descendant_with_class(self):
        html = "<div class='container'><button class='btn'>Go</button></div>"
        el = qs(html, ".container .btn")
        assert el is not None

    def test_descendant_no_match(self):
        html = "<div><span>x</span></div><form><input></form>"
        assert qs(html, "form span") is None


# ─── Child combinator ─────────────────────────────────────────────────────────

class TestChildCombinator:
    def test_direct_child(self):
        html = "<form><input id='direct'></form>"
        el = qs(html, "form > input")
        assert el is not None

    def test_not_direct_child(self):
        # input is nested inside a div inside form — NOT a direct child of form
        html = "<form><div><input id='nested'></div></form>"
        el = qs(html, "form > input")
        assert el is None

    def test_child_with_class(self):
        html = "<nav><a class='active' href='/'>Home</a></nav>"
        el = qs(html, "nav > a.active")
        assert el is not None


# ─── Comma group selector ─────────────────────────────────────────────────────

class TestGroupSelector:
    def test_button_or_a(self):
        html = "<button>B</button><a href='/'>L</a><span>S</span>"
        els = qsa(html, "button, a")
        tags = {e.tag for e in els}
        assert "button" in tags
        assert "a" in tags
        assert "span" not in tags

    def test_id_or_class(self):
        html = "<div id='a'>a</div><div class='b'>b</div><div>c</div>"
        els = qsa(html, "#a, .b")
        assert len(els) == 2


# ─── Pseudo-class selectors ───────────────────────────────────────────────────

class TestPseudoClass:
    def test_disabled(self):
        html = "<input disabled><input id='ok'>"
        els = qsa(html, "input:disabled")
        assert len(els) == 1
        assert els[0].get_id() != "ok"

    def test_checked(self):
        html = "<input type='checkbox' checked><input type='checkbox'>"
        els = qsa(html, "input:checked")
        assert len(els) == 1

    def test_first_child(self):
        html = "<ul><li id='a'>a</li><li id='b'>b</li><li id='c'>c</li></ul>"
        el = qs(html, "li:first-child")
        assert el is not None
        assert el.get_id() == "a"

    def test_last_child(self):
        html = "<ul><li id='a'>a</li><li id='b'>b</li><li id='c'>c</li></ul>"
        el = qs(html, "li:last-child")
        assert el is not None
        assert el.get_id() == "c"

    def test_nth_child_exact(self):
        html = "<ul><li id='a'>a</li><li id='b'>b</li><li id='c'>c</li></ul>"
        el = qs(html, "li:nth-child(2)")
        assert el is not None
        assert el.get_id() == "b"

    def test_nth_child_odd(self):
        html = "<ul><li>1</li><li>2</li><li>3</li><li>4</li></ul>"
        els = qsa(html, "li:nth-child(odd)")
        assert len(els) == 2

    def test_nth_child_even(self):
        html = "<ul><li>1</li><li>2</li><li>3</li><li>4</li></ul>"
        els = qsa(html, "li:nth-child(even)")
        assert len(els) == 2

    def test_hidden_pseudo(self):
        html = "<div style='display:none' id='h'>hidden</div><div id='v'>visible</div>"
        els = qsa(html, "div:hidden")
        hidden_ids = {e.get_id() for e in els}
        assert "h" in hidden_ids
        assert "v" not in hidden_ids


# ─── Real-world selectors ─────────────────────────────────────────────────────

class TestRealWorld:
    LOGIN_HTML = """
    <html><body>
      <form id="login-form" action="/login" method="post">
        <input id="email" type="email" name="email" placeholder="Email">
        <input id="pw" type="password" name="password">
        <button type="submit" class="btn-primary" id="submit">Sign In</button>
      </form>
      <a href="/forgot" class="link-secondary">Forgot password?</a>
    </body></html>
    """

    def test_select_email_by_id(self):
        doc = parse_html(self.LOGIN_HTML)
        el = SelectorEngine().query_selector(doc, "#email")
        assert el is not None
        assert el.get_attribute("type") == "email"

    def test_select_submit_button(self):
        doc = parse_html(self.LOGIN_HTML)
        el = SelectorEngine().query_selector(doc, "button[type=submit]")
        assert el is not None
        assert el.get_id() == "submit"

    def test_select_form_inputs(self):
        doc = parse_html(self.LOGIN_HTML)
        els = SelectorEngine().query_selector_all(doc, "form input")
        assert len(els) == 2

    def test_select_primary_button(self):
        doc = parse_html(self.LOGIN_HTML)
        el = SelectorEngine().query_selector(doc, "button.btn-primary")
        assert el is not None

    def test_select_all_interactive(self):
        doc = parse_html(self.LOGIN_HTML)
        els = SelectorEngine().query_selector_all(doc, "input, button, a")
        assert len(els) >= 4  # 2 inputs + 1 button + 1 link

    def test_form_direct_children(self):
        doc = parse_html(self.LOGIN_HTML)
        # button is a direct child of form
        el = SelectorEngine().query_selector(doc, "form > button")
        assert el is not None
