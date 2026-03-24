"""Shared pytest fixtures for AN-Web tests."""
from __future__ import annotations

import pytest
from an_web.dom.nodes import Document, Element, TextNode


@pytest.fixture
def simple_document() -> Document:
    """A minimal Document with a login form structure."""
    doc = Document(url="https://example.com/login")
    doc.title = "Login"

    html = Element(node_id="html", tag="html")
    body = Element(node_id="body", tag="body")
    form = Element(node_id="form1", tag="form", attributes={"id": "login-form"})

    email_input = Element(
        node_id="inp1", tag="input",
        attributes={"type": "email", "name": "email", "placeholder": "Email"},
    )
    pwd_input = Element(
        node_id="inp2", tag="input",
        attributes={"type": "password", "name": "password", "placeholder": "Password"},
    )
    btn = Element(
        node_id="btn1", tag="button",
        attributes={"type": "submit", "class": "btn-primary"},
    )
    btn.append_child(TextNode(node_id="t1", data="Log In"))

    form.append_child(email_input)
    form.append_child(pwd_input)
    form.append_child(btn)
    body.append_child(form)
    html.append_child(body)
    doc.append_child(html)

    for el in [html, body, form, email_input, pwd_input, btn]:
        doc.register_element(el)
        el.visibility_state = "visible"
        el.is_interactive = el.tag in ("input", "button", "a", "select", "textarea")

    return doc
