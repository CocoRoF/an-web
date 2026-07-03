"""Unit tests for HTML parser bridge."""
from __future__ import annotations

from an_web.browser.parser import parse_html
from an_web.dom.nodes import CommentNode, Document, Element, TextNode


class TestParseHtmlBasic:
    def test_returns_document(self):
        doc = parse_html("<html><body><p>hi</p></body></html>")
        assert isinstance(doc, Document)

    def test_base_url_stored(self):
        doc = parse_html("<html></html>", base_url="https://example.com")
        assert doc.url == "https://example.com"

    def test_title_extracted(self):
        doc = parse_html("<html><head><title>My Page</title></head><body></body></html>")
        assert doc.title == "My Page"

    def test_empty_html(self):
        doc = parse_html("")
        assert isinstance(doc, Document)

    def test_minimal_html(self):
        doc = parse_html("<p>Hello</p>")
        elements = list(doc.iter_elements())
        assert any(el.tag == "p" for el in elements)


class TestParseHtmlStructure:
    def test_nested_structure(self):
        html = "<div id='outer'><div id='inner'><span>text</span></div></div>"
        doc = parse_html(html)
        elements = list(doc.iter_elements())
        tags = [el.tag for el in elements]
        assert "div" in tags
        assert "span" in tags

    def test_parent_child_relationship(self):
        html = "<form><input type='text' name='q'><button>Go</button></form>"
        doc = parse_html(html)
        # form should be an ancestor of input and button
        form = next((el for el in doc.iter_elements() if el.tag == "form"), None)
        assert form is not None
        input_el = next((el for el in doc.iter_elements() if el.tag == "input"), None)
        assert input_el is not None
        # input must be a descendant of form
        descendants = list(form.iter_descendants())
        assert input_el in descendants

    def test_attributes_preserved(self):
        html = "<input type='email' name='email' placeholder='Enter email'>"
        doc = parse_html(html)
        inp = next((el for el in doc.iter_elements() if el.tag == "input"), None)
        assert inp is not None
        assert inp.get_attribute("type") == "email"
        assert inp.get_attribute("name") == "email"
        assert inp.get_attribute("placeholder") == "Enter email"

    def test_id_attribute(self):
        html = "<button id='submit-btn'>Submit</button>"
        doc = parse_html(html)
        el = doc.get_element_by_id("submit-btn")
        assert el is not None
        assert el.tag == "button"

    def test_class_attribute(self):
        html = "<div class='container main'></div>"
        doc = parse_html(html)
        div = next((el for el in doc.iter_elements() if el.tag == "div"), None)
        assert div is not None
        assert "container" in div.get_class_list()
        assert "main" in div.get_class_list()


class TestParseHtmlTextContent:
    def test_text_content_accessible(self):
        html = "<p>Hello world</p>"
        doc = parse_html(html)
        p = next((el for el in doc.iter_elements() if el.tag == "p"), None)
        assert p is not None
        assert "Hello world" in p.text_content

    def test_button_text(self):
        html = "<button type='submit'>Log In</button>"
        doc = parse_html(html)
        btn = next((el for el in doc.iter_elements() if el.tag == "button"), None)
        assert btn is not None
        assert "Log In" in btn.text_content


class TestParseHtmlInteractivity:
    def test_input_is_interactive(self):
        html = "<input type='text'>"
        doc = parse_html(html)
        inp = next((el for el in doc.iter_elements() if el.tag == "input"), None)
        assert inp is not None
        assert inp.is_interactive is True

    def test_button_is_interactive(self):
        html = "<button>Click</button>"
        doc = parse_html(html)
        btn = next((el for el in doc.iter_elements() if el.tag == "button"), None)
        assert btn is not None
        assert btn.is_interactive is True

    def test_div_not_interactive(self):
        html = "<div>content</div>"
        doc = parse_html(html)
        div = next((el for el in doc.iter_elements() if el.tag == "div"), None)
        assert div is not None
        assert div.is_interactive is False

    def test_anchor_is_interactive(self):
        html = "<a href='/page'>Link</a>"
        doc = parse_html(html)
        a = next((el for el in doc.iter_elements() if el.tag == "a"), None)
        assert a is not None
        assert a.is_interactive is True


class TestParseHtmlVisibility:
    def test_hidden_input_not_visible(self):
        html = "<input type='hidden' name='token' value='abc'>"
        doc = parse_html(html)
        inp = next((el for el in doc.iter_elements() if el.tag == "input"), None)
        assert inp is not None
        assert inp.visibility_state == "none"

    def test_display_none_hidden(self):
        html = "<div style='display: none'>hidden</div>"
        doc = parse_html(html)
        div = next((el for el in doc.iter_elements() if el.tag == "div"), None)
        assert div is not None
        assert div.visibility_state == "none"


class TestParseHtmlSkippedTags:
    def test_script_preserved_in_dom(self):
        """Script tags are kept in DOM for external script discovery."""
        html = "<html><body><script>alert(1)</script><p>text</p></body></html>"
        doc = parse_html(html)
        tags = {el.tag for el in doc.iter_elements()}
        assert "script" in tags

    def test_script_invisible(self):
        """Script tags should be invisible."""
        html = "<html><body><script>alert(1)</script><p>text</p></body></html>"
        doc = parse_html(html)
        script = next((el for el in doc.iter_elements() if el.tag == "script"), None)
        assert script is not None
        assert script.visibility_state == "none"

    def test_style_kept_hidden_and_empty(self):
        # v0.7.2: style/template/noscript/meta stay in the DOM (hidden,
        # childless) so React hydration's childNodes walk matches a real
        # browser — but their CSS/inert text must NOT reach text surfaces.
        html = "<html><head><style>body{color:red}</style></head><body><p>hi</p></body></html>"
        doc = parse_html(html)
        style = next((el for el in doc.iter_elements() if el.tag == "style"), None)
        assert style is not None
        assert style.visibility_state == "none"
        assert style.children == []
        body = doc.body
        assert body is not None and "color" not in body.text_content


class TestParseHtmlLoginForm:
    """Real-world login form parsing."""

    LOGIN_HTML = """
    <!DOCTYPE html>
    <html>
    <head><title>Login</title></head>
    <body>
      <form id="login-form" action="/login" method="post">
        <label for="email">Email</label>
        <input id="email" type="email" name="email" placeholder="you@example.com">
        <label for="password">Password</label>
        <input id="password" type="password" name="password">
        <button type="submit" class="btn-primary">Sign In</button>
      </form>
    </body>
    </html>
    """

    def test_title(self):
        doc = parse_html(self.LOGIN_HTML)
        assert doc.title == "Login"

    def test_form_present(self):
        doc = parse_html(self.LOGIN_HTML)
        form = next((el for el in doc.iter_elements() if el.tag == "form"), None)
        assert form is not None
        assert form.get_attribute("id") == "login-form"

    def test_inputs_present(self):
        doc = parse_html(self.LOGIN_HTML)
        inputs = [el for el in doc.iter_elements() if el.tag == "input"]
        assert len(inputs) == 2

    def test_email_input(self):
        doc = parse_html(self.LOGIN_HTML)
        email = doc.get_element_by_id("email")
        assert email is not None
        assert email.get_attribute("type") == "email"

    def test_button_present(self):
        doc = parse_html(self.LOGIN_HTML)
        btn = next((el for el in doc.iter_elements() if el.tag == "button"), None)
        assert btn is not None
        assert btn.get_attribute("class") == "btn-primary"

    def test_all_interactive_detected(self):
        doc = parse_html(self.LOGIN_HTML)
        interactive = [el for el in doc.iter_elements() if el.is_interactive]
        # email input, password input, button
        assert len(interactive) >= 3


class TestInlineTextInterleaving:
    """Reading order of mixed inline content (v0.7.2 P0 fix).

    The old css("*") walk concatenated all direct text of an element into a
    single TextNode appended before its element children, destroying reading
    order on virtually every text-heavy page.
    """

    MIXED = (
        '<html><body><p id="t">Python is a <a href="/x">high-level</a>, '
        '<a href="/y">general-purpose</a>\n<b>programming language</b> that '
        'emphasizes <i>readability</i> and simplicity.</p></body></html>'
    )

    EXPECTED = ("Python is a high-level, general-purpose programming language "
                "that emphasizes readability and simplicity.")

    def _paragraph(self, doc: Document) -> Element:
        for el in doc.iter_elements():
            if el.tag == "p":
                return el
        raise AssertionError("no <p> parsed")

    def test_text_content_preserves_reading_order(self):
        doc = parse_html(self.MIXED)
        p = self._paragraph(doc)
        assert " ".join(p.text_content.split()) == self.EXPECTED

    def test_inner_text_preserves_reading_order(self):
        doc = parse_html(self.MIXED)
        p = self._paragraph(doc)
        assert " ".join(p.inner_text.split()) == self.EXPECTED

    def test_children_interleaved_not_grouped(self):
        doc = parse_html(self.MIXED)
        p = self._paragraph(doc)
        kinds = ["text" if isinstance(c, TextNode) else c.tag for c in p.children]
        # text, a, text(","), a, text(ws-separator), b, text, i, text
        assert kinds == ["text", "a", "text", "a", "text", "b", "text", "i", "text"]

    def test_boundary_whitespace_kept_in_text_nodes(self):
        doc = parse_html("<p>foo <b>bar</b> baz</p>")
        p = next(el for el in doc.iter_elements() if el.tag == "p")
        assert p.text_content == "foo bar baz"

    def test_whitespace_only_node_between_inline_elements_kept(self):
        doc = parse_html("<p><a>one</a>\n<a>two</a></p>")
        p = next(el for el in doc.iter_elements() if el.tag == "p")
        assert " ".join(p.text_content.split()) == "one two"

    def test_interblock_indentation_dropped(self):
        doc = parse_html("<div>\n  <p>a</p>\n  <p>b</p>\n</div>")
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        # No stray whitespace-only text nodes between block children
        assert [c.tag for c in div.children if isinstance(c, Element)] == ["p", "p"]
        assert not any(isinstance(c, TextNode) for c in div.children)

    def test_style_text_not_polluting_parent(self):
        doc = parse_html("<div><style>.x{color:red}</style><p>visible</p></div>")
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        assert "color" not in div.text_content
        assert "visible" in div.text_content

    def test_inline_tag_no_word_break_in_inner_text(self):
        doc = parse_html("<p>see <a>link</a>, ok</p>")
        p = next(el for el in doc.iter_elements() if el.tag == "p")
        assert p.inner_text == "see link, ok"


class TestCommentPreservation:
    """Comments must survive parsing (React hydration markers, v0.7.2 P1)."""

    def test_comment_nodes_parsed(self):
        doc = parse_html('<div id="r"><!--$--><p>x</p><!--/$--></div>')
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        kinds = [
            (c.data if isinstance(c, CommentNode) else c.tag)
            for c in div.children
        ]
        assert kinds == ["$", "p", "/$"]

    def test_comment_excluded_from_text_content(self):
        doc = parse_html("<div><!-- hidden -->visible</div>")
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        assert div.text_content == "visible"
        assert div.inner_text == "visible"

    def test_multiline_comment_data(self):
        doc = parse_html("<div><!-- line1\nline2 --></div>")
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        comments = [c for c in div.children if isinstance(c, CommentNode)]
        assert len(comments) == 1
        assert "line1" in comments[0].data and "line2" in comments[0].data

    def test_comment_serialized_to_v8_mirror(self):
        from an_web.js.bridge import _inner_html, marshal_element
        doc = parse_html('<div id="r"><!--$--><p>x</p></div>')
        div = next(el for el in doc.iter_elements() if el.tag == "div")
        marshaled = marshal_element(div)
        node_types = [c["nodeType"] for c in marshaled["children"]]
        assert 8 in node_types
        comment_entry = next(c for c in marshaled["children"] if c["nodeType"] == 8)
        assert comment_entry["data"] == "$"
        assert comment_entry["textContent"] == ""
        assert _inner_html(div) == "<!--$--><p>x</p>"
